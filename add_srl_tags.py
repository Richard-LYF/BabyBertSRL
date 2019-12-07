from spacy.tokens import Doc
import pyprind
from deepsegment import DeepSegment
from typing import List, Generator

from allennlp.predictors.predictor import Predictor
from allennlp.data.instance import Instance

from babybertsrl.io import load_utterances_from_file
from babybertsrl import config
from babybertsrl.job import Params
from babybertsrl.params import param2default

CORPUS_NAME = 'childes-20191206'
INTERACTIVE = False
BATCH_SIZE = 128


def make_srl_string(words: List[str],
                    tags: List[str]) -> str:
    frame = []
    chunk = []

    for (token, tag) in zip(words, tags):
        if tag.startswith("I-"):
            chunk.append(token)
        else:
            if chunk:
                frame.append("[" + " ".join(chunk) + "]")
                chunk = []

            if tag.startswith("B-"):
                chunk.append(tag[2:] + ": " + token)
            elif tag == "O":
                frame.append(token)

    if chunk:
        frame.append("[" + " ".join(chunk) + "]")

    return " ".join(frame)


def gen_instances_from_segment(seg: str,
                               ) -> Generator[Instance, None, None]:
    # POS-tagging
    words = seg.split()
    spacy_doc = Doc(predictor._tokenizer.spacy.vocab, words=words)
    for pipe in filter(None, predictor._tokenizer.spacy.pipeline):
        pipe[1](spacy_doc)  # this does POS tagging

    # to instances - one for each verb in utterance
    tokens = [token for token in spacy_doc]
    res = []
    for i, word in enumerate(tokens):
        if word.pos_ == "VERB":
            verb_labels = [0 for _ in words]
            verb_labels[i] = 1
            instance = predictor._dataset_reader.text_to_instance(tokens, verb_labels)

            yield instance


def gen_instances() -> Generator[Instance, None, None]:
    for u in utterances:
        # possibly segment utterance into multiple well-formed sentences
        words_string = ' '.join(u)
        segments = segmentation.segment(words_string)
        for segment in segments:
            instances = gen_instances_from_segment(segment)
            # in case no verbs are found
            if not instances:
                continue
            else:
                yield from instances


# srl tagger
predictor = Predictor.from_path("https://s3-us-west-2.amazonaws.com/allennlp/models/bert-base-srl-2019.06.17.tar.gz",
                                cuda_device=0)

# segmentation model for splitting ill-formed utterances into well-formed sentences
segmentation = DeepSegment('en')

# utterances
utterances_path = config.Dirs.data / 'CHILDES' / f'{CORPUS_NAME}_mlm.txt'
params = Params.from_param2val(param2default)
utterances = load_utterances_from_file(utterances_path)

it = gen_instances()

progress_bar = pyprind.ProgBar(len(utterances) // BATCH_SIZE, stream=1)
num_no_verb = 0
lines = []
outer_loop = True
while outer_loop:

    # fill batch with instances
    batch = []
    while len(batch) < BATCH_SIZE:
        try:
            instance = next(it)
        except StopIteration:
            outer_loop = False
            break
        batch.append(instance)

    # get SRL predictions for batch of instances
    res = predictor._model.forward_on_instances(batch)

    # make a line for each instance
    for d in res:
        tags = d['tags']
        words = d['words']

        # sometimes there is no B-V
        if 'B-V' not in tags:
            num_no_verb += 1
            continue

        # make line
        verb_index = tags.index('B-V')
        x_string = " ".join(words)
        y_string = " ".join(tags)
        line = f'{verb_index} {x_string} ||| {y_string}'

        if INTERACTIVE:
            print('=====================================')
            print(make_srl_string(words, tags))
            print(line)
            key = input('\n[q] to quit. Any key to continue.\n')
            if key != 'q':
                pass
            else:
                raise SystemExit('Quit')

        lines.append(line)

    progress_bar.update()

print(f'Skipped {num_no_verb} utterances due to absence of B-V tag')

print(f'Writing {len(lines)} lines to file...')
srl_path = config.Dirs.data / 'CHILDES' / f'{CORPUS_NAME}_srl.txt'
with srl_path.open('w') as f:
    for line in lines:
        f.write(line + '\n')

