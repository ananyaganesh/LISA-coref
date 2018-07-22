import tensorflow as tf
import argparse
import dataset
from vocab import Vocab
import os
from model import LISAModel
from functools import partial
import train_utils


arg_parser = argparse.ArgumentParser(description='')
arg_parser.add_argument('--train_file', type=str, help='Training data file')
arg_parser.add_argument('--dev_file', type=str, help='Development data file')
arg_parser.add_argument('--save_dir', type=str, help='Training data file')
arg_parser.add_argument('--word_embedding_file', type=str, help='File containing pre-trained word embeddings')

args = arg_parser.parse_args()

data_config = {
      'id': {
        'conll_idx': 2,
      },
      'word': {
        'conll_idx': 3,
        'feature': True,
        'vocab': 'glove.6B.100d.txt',
        'converter':  {
          'name': 'lowercase'
        },
        'oov': True
      },
      'auto_pos': {
        'conll_idx': 4,
        'vocab': 'gold_pos'
      },
      'gold_pos': {
        'conll_idx': 5,
        'label': True,
        'vocab': 'gold_pos'
      },
      'parse_head': {
        'conll_idx': [6, 2],
        'label': True,
        'converter':  {
          'name': 'parse_roots_self_loop'
        }
      },
      'parse_label': {
        'conll_idx': 7,
        'label': True,
        'vocab': 'parse_label'
      },
      'domain': {
        'conll_idx': 0,
        'vocab': 'domain',
        'converter': {
          'name': 'strip_conll12_domain'
        }
      },
      'predicate': {
        'conll_idx': 10,
        'label': True,
        'vocab': 'predicate',
        'converter': {
          'name': 'conll12_binary_predicates'
        }
      },
      'joint_pos_predicate': {
        'conll_idx': [5, 10],
        'label': True,
        'vocab': 'joint_pos_predicate',
        'converter': {
          'name': 'joint_converter',
          'params': {
            'component_converters': ['default_converter', 'conll12_binary_predicates']
          }
        },
        'label_components': [
          'gold_pos',
          'predicate'
        ]
      },
      'srl': {
        'conll_idx': [14, -1],
        'type': 'range',
        'label': True,
        'vocab': 'srl',
        'converter': {
          'name': 'idx_range_converter'
        }
      },
    }

model_config = {
  'num_layers': 12,
  'input_dropout': 0.8,
  'predicate_mlp_size': 200,
  'role_mlp_size': 200,
  'predicate_pred_mlp_size': 200,
  'word_embedding_size': 100,
  'layers': {
    'type': 'transformer',
    'num_heads': 8,
    'head_dim': 25,
    'ff_hidden_size': 800,
    'attn_dropout': 0.9,
    'ff_dropout': 0.9,
    'prepost_dropout': 0.9,
  }
}

# task_config = {
#   'gold_pos': {
#     'layer': 3,
#   },
#   'predicate': {
#     'layer': 3,
#   },
#   'parse_head': {
#     'layer': 5,
#   },
#   'parse_label': {
#     'layer': 5,
#   },
#   'srl': {
#     'layer': 12
#   },
# }
# todo validate these files
task_config = {
  3: {
    'joint_pos_predicate': {
      'penalty': 1.0,
      'output_fn': {
        'name': 'joint_softmax_classifier',
        'params': {
          'joint_maps': {
            'maps': [
              'joint_pos_predicate_to_gold_pos',
              'joint_pos_predicate_to_predicate'
            ]
          }
        }
      },
      'eval_fn': {
        'name': 'accuracy',
        'params': {}
      }
    }
  },

  # 5: {
  #   'parse_head',
  #   'parse_label'
  # },

  # 12: {
  #   'srl': {
  #     'penalty': 1.0,
  #     'output_fn': {
  #       'name': 'srl_bilinear',
  #       'params': {
  #         'predicate_preds': {
  #           'layer': 'joint_pos_predicate',
  #           'output': 'predicate_predictions'
  #         }
  #       }
  #     },
  #     'eval_fn': {
  #       'name': 'accuracy',
  #       'params': {}
  #     }
  #   }
  # }
}

num_train_epochs = 50
batch_size = 256

if not os.path.exists(args.save_dir):
    os.makedirs(args.save_dir)

tf.logging.set_verbosity(tf.logging.INFO)

train_vocab = Vocab(args.train_file, data_config, args.save_dir)

def get_input_fn(data_file, num_epochs, is_train):
  # this needs to be created from here so that it ends up in the same tf.Graph as everything else
  vocab_lookup_ops = train_vocab.create_vocab_lookup_ops(args.word_embedding_file) if args.word_embedding_file \
    else train_vocab.create_vocab_lookup_ops()

  return dataset.get_data_iterator(data_file, data_config, vocab_lookup_ops, batch_size, num_epochs, is_train)


def train_input_fn():
  return get_input_fn(args.train_file, num_epochs=1, is_train=True)


def dev_input_fn():
  return get_input_fn(args.dev_file, num_epochs=1, is_train=False)


feature_idx_map = {f: i for i, f in enumerate([d for d in data_config.keys() if
                           ('feature' in data_config[d] and data_config[d]['feature']) or
                           ('label' in data_config[d] and data_config[d]['label'])])
                   if 'feature' in data_config[f] and data_config[f]['feature']}

label_idx_map = {f: i for i, f in enumerate([d for d in data_config.keys() if \
                           ('feature' in data_config[d] and data_config[d]['feature']) or \
                           ('label' in data_config[d] and data_config[d]['label'])])
                 if 'label' in data_config[f] and data_config[f]['label']}

model = LISAModel(args, model_config, task_config, feature_idx_map, label_idx_map, train_vocab)

num_train_examples = 39832  # todo: compute this automatically
evaluate_every_n_epochs = 5
num_steps_in_epoch = int(num_train_examples / batch_size)
eval_every_steps = evaluate_every_n_epochs * num_steps_in_epoch
tf.logging.log(tf.logging.INFO, "Evaluating every %d steps" % eval_every_steps)

checkpointing_config = tf.estimator.RunConfig(save_checkpoints_steps=eval_every_steps, keep_checkpoint_max=1)
estimator = tf.estimator.Estimator(model_fn=model.model_fn, model_dir=args.save_dir, config=checkpointing_config)

# validation_hook = ValidationHook(estimator, dev_input_fn, every_n_steps=save_and_eval_every)

save_best_exporter = tf.estimator.BestExporter(compare_fn=partial(train_utils.best_model_compare_fn, key="acc"),
                                               serving_input_receiver_fn=train_utils.serving_input_receiver_fn)
train_spec = tf.estimator.TrainSpec(input_fn=train_input_fn, max_steps=num_steps_in_epoch*num_train_epochs)
eval_spec = tf.estimator.EvalSpec(input_fn=dev_input_fn, exporters=[save_best_exporter])

tf.estimator.train_and_evaluate(estimator, train_spec, eval_spec)

# estimator.train(input_fn=train_input_fn, steps=100000, hooks=[validation_hook])
# estimator.evaluate(input_fn=train_input_fn)


# np.set_printoptions(threshold=np.inf)
# with tf.Session() as sess:
#   sess.run(tf.tables_initializer())
#   for i in range(3):
#     print(sess.run(input_fn()))

