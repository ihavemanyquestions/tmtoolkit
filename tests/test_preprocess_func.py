"""
Preprocessing: Functional API tests.
"""

from collections import Counter
import math
import string
import random

from hypothesis import given, strategies as st
import pytest
import numpy as np
from spacy.tokens import Doc
from nltk.corpus import wordnet as wn
from scipy.sparse import isspmatrix_coo
import nltk

from ._testtools import strategy_texts, strategy_tokens
from ._testcorpora import corpora_sm

from tmtoolkit._pd_dt_compat import USE_DT, FRAME_TYPE, pd_dt_colnames
from tmtoolkit.utils import flatten_list
from tmtoolkit.preprocess import (DEFAULT_LANGUAGE_MODELS, init_for_language, tokenize, doc_labels, doc_lengths,
    vocabulary, vocabulary_counts, doc_frequencies, ngrams, sparse_dtm, kwic, kwic_table, glue_tokens, simplified_pos,
    tokens2ids, ids2tokens, pos_tag_convert_penn_to_wn, str_multisplit, str_shape, str_shapesplit,
    expand_compound_token, remove_chars, make_index_window_around_matches,
    token_match_subsequent, token_glue_subsequent, transform, to_lowercase, pos_tag, lemmatize, expand_compounds,
    clean_tokens, filter_tokens, filter_documents, filter_documents_by_name, filter_for_pos, filter_tokens_by_mask,
    remove_common_tokens, remove_uncommon_tokens, token_match, filter_tokens_with_kwic
)
from tmtoolkit.preprocess._common import _filtered_docs_tokens, _filtered_doc_tokens


LANGUAGE_CODES = list(sorted(DEFAULT_LANGUAGE_MODELS.keys()))


@pytest.fixture(scope='module', params=LANGUAGE_CODES)
def nlp_all(request):
    return init_for_language(request.param)


@pytest.fixture()
def tokens_en():
    _init_lang('en')
    return tokenize(corpora_sm['en'])


@pytest.fixture()
def tokens_mini():
    _init_lang('en')
    corpus = {'ny': 'I live in New York.',
              'bln': 'I am in Berlin, but my flat is in Munich.',
              'empty': ''}
    return tokenize(corpus)


def test_init_for_language():
    # note: this requires all language models to be installed

    with pytest.raises(ValueError, match='either .+ must be given'):
        init_for_language()

    with pytest.raises(ValueError, match='two-letter ISO 639-1 language code'):
        init_for_language('foo')

    with pytest.raises(ValueError, match='is not supported'):
        init_for_language('xx')

    with pytest.raises(OSError, match="Can't find model"):
        init_for_language(language_model='foobar')

    # try loading by language code / language model
    for i, (lang, model) in enumerate(DEFAULT_LANGUAGE_MODELS.items()):
        kwargs = {'language': lang} if i % 2 == 0 else {'language_model': model}

        nlp = init_for_language(**kwargs)

        assert nlp is not None
        assert str(nlp.__class__).startswith("<class 'spacy.lang.")
        assert len(nlp.pipeline) == 1 and nlp.pipeline[0][0] == 'tagger'   # default pipeline only with tagger

    # pass custom param to spacy.load: don't disable anything in the pipeline
    nlp = init_for_language(language='en', disable=[])
    assert len(nlp.pipeline) == 3   # tagger, parser, ner


@pytest.mark.parametrize(
    'testcase, docs, doc_labels, doc_labels_fmt',
    [
        (0, [], None, None),
        (1, [], None, None),
        (2, [''], None, None),
        (3, ['', ''], None, None),
        (4, ['Simple test.'], None, None),
        (5, ['Simple test.', 'Here comes another document.'], None, None),
        (6, ['Simple test.', 'Here comes another document.'], list('ab'), None),
        (7, ['Simple test.', 'Here comes another document.'], None, 'foo_{i0}'),
        (8, {'a': 'Simple test.', 'b': 'Here comes another document.'}, None, None),
    ]
)
def test_tokenize(testcase, docs, doc_labels, doc_labels_fmt):
    if testcase == 0:
        _init_lang(None)

        with pytest.raises(ValueError):
            tokenize(docs)

        return

    _init_lang('en')

    kwargs = {}
    if doc_labels is not None:
        kwargs['doc_labels'] = doc_labels
    if doc_labels_fmt is not None:
        kwargs['doc_labels_fmt'] = doc_labels_fmt

    res = tokenize(docs, **kwargs)

    assert isinstance(res, list)
    assert len(res) == len(docs)
    assert all([isinstance(d, Doc) for d in res])
    assert all([isinstance(d._.label, str) for d in res])     # each doc. has a string label
    # Doc object text must be same as raw text
    assert all([d.text == txt for d, txt in zip(res, docs.values() if isinstance(docs, dict) else docs)])

    if testcase == 1:
        assert len(res) == 0
    elif testcase in {2, 3}:
        assert all([len(d) == 0 for d in res])
    elif testcase == 4:
        assert len(res) == 1
        assert len(res[0]) == 3
    elif testcase == 5:
        assert len(res) == 2
        assert all([d._.label == ('doc-%d' % (i+1)) for i, d in enumerate(res)])
    elif testcase == 6:
        assert len(res) == 2
        assert all([d._.label == lbl for d, lbl in zip(res, doc_labels)])
    elif testcase == 7:
        assert len(res) == 2
        assert all([d._.label == ('foo_%d' % i) for i, d in enumerate(res)])
    elif testcase == 8:
        assert len(res) == 2
        assert all([d._.label == lbl for d, lbl in zip(res, docs.keys())])
    else:
        raise RuntimeError('testcase not covered:', testcase)


def test_tokenize_all_languages(nlp_all):
    firstwords = {
        'en': ('NewsArticles-1', 'Disney'),
        'de': ('sample-9611', 'Sehr'),
        'fr': ('sample-1', "L'"),
        'es': ('sample-1', "El"),
        'pt': ('sample-1', "O"),
        'it': ('sample-1', "Bruce"),
        'nl': ('sample-1', "Cristiano"),
        'el': ('sample-1', "Ο"),
        'nb': ('sample-1', "Frøyningsfjelltromma"),
        'lt': ('sample-1', "Klondaiko"),
    }

    corpus = corpora_sm[nlp_all.lang]

    res = tokenize(corpus)
    assert isinstance(res, list)
    assert len(res) == len(corpus)

    assert all([isinstance(d, Doc) for d in res])
    assert all([isinstance(d._.label, str) for d in res])     # each doc. has a string label

    for doc in res:
        for arrname in ('tokens', 'mask'):
            assert arrname in doc.user_data and isinstance(doc.user_data[arrname], np.ndarray)

        assert np.issubdtype(doc.user_data['mask'].dtype, np.bool_)
        assert np.all(doc.user_data['mask'])
        assert np.issubdtype(doc.user_data['tokens'].dtype, np.str_)

    res_dict = {d._.label: d for d in res}

    firstdoc_label, firstword = firstwords[nlp_all.lang]
    firstdoc = res_dict[firstdoc_label]
    assert len(firstdoc) > 0
    assert firstdoc[0].text == firstword

    for dl, txt in corpus.items():
        d = res_dict[dl]
        assert d.text == txt


def test_doc_labels(tokens_en):
    assert set(doc_labels(tokens_en)) == set(corpora_sm['en'])

    _init_lang('en')
    docs = tokenize(['test doc 1', 'test doc 2', 'test doc 3'], doc_labels=list('abc'))
    assert doc_labels(docs) == list('abc')


def test_doc_lengths(tokens_en):
    res = doc_lengths(tokens_en)
    assert isinstance(res, list)
    assert len(res) == len(tokens_en)

    for n, d in zip(res, tokens_en):
        assert isinstance(n, int)
        assert n == len(d)
        if d._.label == 'empty':
            assert n == len(d) == 0


@pytest.mark.parametrize('sort', [False, True])
def test_vocabulary(tokens_en, sort):
    res = vocabulary(tokens_en, sort=sort)

    if sort:
        assert isinstance(res, list)
        assert sorted(res) == res
    else:
        assert isinstance(res, set)

    for t in res:
        assert isinstance(t, str)
        assert any([t in dtok for dtok in _filtered_docs_tokens(tokens_en)])


def test_vocabulary_counts(tokens_en):
    res = vocabulary_counts(tokens_en)
    n_tok = sum(doc_lengths(tokens_en))

    assert isinstance(res, Counter)
    assert set(res.keys()) == vocabulary(tokens_en)
    assert all([0 < n <= n_tok for n in res.values()])
    assert any([n > 1 for n in res.values()])


@pytest.mark.parametrize('proportions', [False, True])
def test_doc_frequencies(tokens_en, proportions):
    res = doc_frequencies(tokens_en, proportions=proportions)

    assert set(res.keys()) == vocabulary(tokens_en)

    if proportions:
        assert isinstance(res, dict)
        assert all([0 < v <= 1 for v in res.values()])
    else:
        assert isinstance(res, Counter)
        assert all([0 < v < len(tokens_en) for v in res.values()])
        assert any([v > 1 for v in res.values()])


def test_doc_frequencies_example():
    docs = [   # also works with simple token lists
        list('abc'),
        list('abb'),
        list('ccc'),
        list('da'),
    ]

    abs_df = doc_frequencies(docs)
    assert dict(abs_df) == {
        'a': 3,
        'b': 2,
        'c': 2,
        'd': 1,
    }

    rel_df = doc_frequencies(docs, proportions=True)
    math.isclose(rel_df['a'], 3/4)
    math.isclose(rel_df['b'], 2/4)
    math.isclose(rel_df['c'], 2/4)
    math.isclose(rel_df['d'], 1/4)


@pytest.mark.parametrize('pass_vocab', [False, True])
def test_sparse_dtm(tokens_en, pass_vocab):
    if pass_vocab:
        vocab = vocabulary(tokens_en, sort=True)
        dtm = sparse_dtm(tokens_en, vocab)
    else:
        dtm, vocab = sparse_dtm(tokens_en)

    assert isspmatrix_coo(dtm)
    assert dtm.shape == (len(tokens_en), len(vocab))
    assert vocab == vocabulary(tokens_en, sort=True)

    doc_ntok = dtm.sum(axis=1)
    assert doc_ntok[[d._.label for d in tokens_en].index('empty'), 0] == 0


def test_sparse_dtm_example():
    docs = [
        list('abc'),
        list('abb'),
        list('ccc'),
        [],
        list('da'),
    ]

    dtm, vocab = sparse_dtm(docs)
    assert vocab == list('abcd')
    assert dtm.shape == (5, 4)

    assert np.array_equal(dtm.A, np.array([
        [1, 1, 1, 0],
        [1, 2, 0, 0],
        [0, 0, 3, 0],
        [0, 0, 0, 0],
        [1, 0, 0, 1],
    ]))


@pytest.mark.parametrize('n', list(range(0, 5)))
def test_ngrams(tokens_en, n):
    if n < 2:
        with pytest.raises(ValueError):
            ngrams(tokens_en, n)
    else:
        docs_unigrams = _filtered_docs_tokens(tokens_en)
        docs_ng = ngrams(tokens_en, n, join=False)
        docs_ng_joined = ngrams(tokens_en, n, join=True, join_str='')
        assert len(docs_ng) == len(docs_ng_joined) == len(docs_unigrams)

        for doc_ng, doc_ng_joined, tok in zip(docs_ng, docs_ng_joined, docs_unigrams):
            n_tok = len(tok)

            assert len(doc_ng_joined) == len(doc_ng)
            assert all([isinstance(x, list) for x in doc_ng])
            assert all([isinstance(x, str) for x in doc_ng_joined])

            if n_tok < n:
                if n_tok == 0:
                    assert doc_ng == doc_ng_joined == []
                else:
                    assert len(doc_ng) == len(doc_ng_joined) == 1
                    assert doc_ng == [tok]
                    assert doc_ng_joined == [''.join(tok)]
            else:
                assert len(doc_ng) == len(doc_ng_joined) == n_tok - n + 1
                assert all([len(g) == n for g in doc_ng])
                assert all([''.join(g) == gj for g, gj in zip(doc_ng, doc_ng_joined)])

                tokens_ = list(doc_ng[0])
                if len(doc_ng) > 1:
                    tokens_ += [g[-1] for g in doc_ng[1:]]
                assert tokens_ == tok.tolist()


@given(search_term_exists=st.booleans(), context_size=st.integers(1, 5), as_dict=st.booleans(),
       non_empty=st.booleans(), glue=st.booleans(), highlight_keyword=st.booleans())
def test_kwic(tokens_en, search_term_exists, context_size, as_dict, non_empty, glue, highlight_keyword):
    vocab = list(vocabulary(tokens_en))

    if search_term_exists and len(vocab) > 0:
        s = random.choice(vocab)
    else:
        s = 'thisdoesnotexist'

    res = kwic(tokens_en, s, context_size=context_size, non_empty=non_empty, as_dict=as_dict,
               glue=' ' if glue else None,
               highlight_keyword='*' if highlight_keyword else None)

    assert isinstance(res, dict if as_dict else list)

    if as_dict:
        if non_empty:
            assert all(k in doc_labels(tokens_en) for k in res.keys())
        else:
            assert list(res.keys()) == doc_labels(tokens_en)
        res = res.values()

    if s in vocab:
        for win in res:
            if non_empty:
                assert len(win) > 0

            for w in win:
                if highlight_keyword:
                    assert '*' + s + '*' in w
                else:
                    assert s in w

                if not glue:
                    assert 0 <= len(w) <= context_size * 2 + 1
    else:
        if non_empty:
            assert len(res) == 0
        else:
            assert all([n == 0 for n in map(len, res)])


def test_kwic_example(tokens_mini):
    res = kwic(tokens_mini, 'in', context_size=1)
    assert res == [
        [['live', 'in', 'New']],
        [['am', 'in', 'Berlin'], ['is', 'in', 'Munich']],
        [],
    ]

    res = kwic(tokens_mini, 'in', context_size=1, as_dict=True)
    assert res == {
        'ny': [['live', 'in', 'New']],
        'bln': [['am', 'in', 'Berlin'], ['is', 'in', 'Munich']],
        'empty': [],
    }

    res = kwic(tokens_mini, 'in', context_size=1, non_empty=True)
    assert res == [
        [['live', 'in', 'New']],
        [['am', 'in', 'Berlin'], ['is', 'in', 'Munich']],
    ]

    res = kwic(tokens_mini, 'in', context_size=1, non_empty=True, glue=' ')
    assert res == [
        ['live in New'],
        ['am in Berlin', 'is in Munich']
    ]

    res = kwic(tokens_mini, 'in', context_size=1, non_empty=True, glue=' ', highlight_keyword='*')
    assert res == [
        ['live *in* New'],
        ['am *in* Berlin', 'is *in* Munich']
    ]

    res = kwic(tokens_mini, 'in', context_size=1, non_empty=True, highlight_keyword='*')
    assert res == [
        [['live', '*in*', 'New']],
        [['am', '*in*', 'Berlin'], ['is', '*in*', 'Munich']],
    ]


@given(search_term_exists=st.booleans(), context_size=st.integers(1, 5))
def test_kwic_table(tokens_en, context_size, search_term_exists):
    vocab = list(vocabulary(tokens_en))

    if search_term_exists and len(vocab) > 0:
        s = random.choice(vocab)
    else:
        s = 'thisdoesnotexist'

    res = kwic_table(tokens_en, s, context_size=context_size)

    assert isinstance(res, FRAME_TYPE)
    assert pd_dt_colnames(res) == ['doc', 'context', 'kwic']

    if s in vocab:
        assert res.shape[0] > 0

        if USE_DT:
            kwic_col = res[:, 'kwic'].to_list()[0]
            docs_col = res[:, 'doc'].to_list()[0]
        else:
            kwic_col = res.loc[:, 'kwic'].to_list()
            docs_col = res.loc[:, 'doc'].to_list()

        assert all(dl in doc_labels(tokens_en) for dl in set(docs_col))

        for kwic_match in kwic_col:
            assert kwic_match.count('*') >= 2
    else:
        assert res.shape[0] == 0


def test_glue_tokens_example(tokens_mini):
    tokens = _filtered_docs_tokens(tokens_mini)
    res = glue_tokens(tokens_mini, ('New', 'York'))
    tokens_ = _filtered_docs_tokens(res)

    assert all([d1 is d2 for d1, d2 in zip(res, tokens_mini)])   # modifies in-place
    for i, (d_, d) in enumerate(zip(tokens_, tokens)):
        d_ = d_.tolist()
        d = d.tolist()
        if i == 0:
            assert d_ == ['I', 'live', 'in', 'New_York', '.']
        else:
            assert d_ == d

    res, glued = glue_tokens(tokens_mini, ('in', '*'), glue='/', match_type='glob', return_glued_tokens=True)
    tokens_ = _filtered_docs_tokens(res)

    assert all([d1 is d2 for d1, d2 in zip(res, tokens_mini)])   # modifies in-place
    assert glued == {'in/New_York', 'in/Berlin', 'in/Munich'}

    for i, (d_, d) in enumerate(zip(tokens_, tokens)):
        d_ = d_.tolist()
        d = d.tolist()
        if i == 0:
            assert d_ == ['I', 'live', 'in/New_York', '.']
        elif i == 1:
            assert d_ == ['I', 'am', 'in/Berlin', ',', 'but', 'my', 'flat', 'is', 'in/Munich', '.']
        else:
            assert d_ == d


@pytest.mark.parametrize(
    'docs, expected',
    [
        ([], []),
        ([['']], [['']]),
        ([[''], []], [[''], []]),
        ([['An', 'US-Student', '.']], [['An', 'US', 'Student', '.']]),
    ]
)
def test_expand_compounds(docs, expected):
    assert expand_compounds(docs) == expected


@given(docs=strategy_tokens(string.printable),
       pass_docs_meta=st.booleans(),
       remove_punct=st.integers(0, 2),
       remove_stopwords=st.integers(0, 2),
       remove_empty=st.booleans(),
       remove_shorter_than=st.integers(-2, 5),
       remove_longer_than=st.integers(-2, 10),
       remove_numbers=st.booleans())
def test_clean_tokens(docs, pass_docs_meta, remove_punct, remove_stopwords, remove_empty, remove_shorter_than,
                      remove_longer_than, remove_numbers):
    if pass_docs_meta:
        docs_meta = [{'meta_pos': np.random.choice(list('ABC'), len(dtok), replace=True).tolist()} for dtok in docs]
    else:
        docs_meta = None

    if remove_punct == 2:
        remove_punct = np.random.choice(list(string.punctuation), 5, replace=False).tolist()
    else:
        remove_punct = bool(remove_punct)

    if remove_stopwords == 2 and len(flatten_list(docs)) > 0:
        remove_stopwords = np.random.choice(flatten_list(docs), 5, replace=True).tolist()
    else:
        remove_stopwords = bool(remove_stopwords)

    if remove_shorter_than == -2:
        remove_shorter_than = None

    if remove_longer_than == -2:
        remove_longer_than = None

    if remove_shorter_than == -1 or remove_longer_than == -1:
        with pytest.raises(ValueError):
            clean_tokens(docs, docs_meta, remove_punct=remove_punct, remove_stopwords=remove_stopwords,
                         remove_empty=remove_empty, remove_shorter_than=remove_shorter_than,
                         remove_longer_than=remove_longer_than, remove_numbers=remove_numbers)
    else:
        res = clean_tokens(docs, docs_meta, remove_punct=remove_punct, remove_stopwords=remove_stopwords,
                           remove_empty=remove_empty, remove_shorter_than=remove_shorter_than,
                           remove_longer_than=remove_longer_than, remove_numbers=remove_numbers)

        blacklist = set()

        if isinstance(remove_punct, list):
            blacklist.update(remove_punct)
        elif remove_punct is True:
            blacklist.update(string.punctuation)

        if isinstance(remove_stopwords, list):
            blacklist.update(remove_stopwords)
        elif remove_stopwords is True:
            blacklist.update(nltk.corpus.stopwords.words('english'))

        if remove_empty:
            blacklist.update('')

        if docs_meta is None:
            docs_ = res
            docs_meta_ = None
        else:
            assert isinstance(res, tuple) and len(res) == 2
            docs_, docs_meta_ = res

            assert isinstance(docs_meta_, list)
            assert len(docs_meta) == len(docs_meta_)

        assert len(docs) == len(docs_)

        for i, (dtok, dtok_) in enumerate(zip(docs, docs_)):
            assert len(dtok) >= len(dtok_)
            del dtok

            if docs_meta_ is not None:
                assert len(dtok_) == len(docs_meta_[i]['meta_pos'])

            assert all([w not in dtok_ for w in blacklist])

            tok_lengths = np.array(list(map(len, dtok_)))

            if remove_shorter_than is not None:
                assert np.all(tok_lengths >= remove_shorter_than)

            if remove_longer_than is not None:
                assert np.all(tok_lengths <= remove_longer_than)

            if remove_numbers and dtok_:
                assert not np.any(np.char.isnumeric(dtok_))


@pytest.mark.parametrize('docs, docs_meta, search_patterns, by_meta, expected_docs, expected_docs_meta', [
    ([], None, 'test', None, [], None),
    ([], [], 'test', None, [], []),
    ([[]], None, 'test', None, [[]], None),
    ([['A', 'test', '!'], ['Teeeest', 'nope']], None, 'test', None, [['test'], []], None),
    ([['A', 'test', '!'], ['Teeeest', 'nope']], [{'meta': list('ABC')}, {'meta': list('CC')}], 'test', None,
     [['test'], []], [{'meta': ['B']}, {'meta': []}]),
    ([['A', 'test', '!'], ['Teeeest', 'nope']], [{'meta': list('ABC')}, {'meta': list('CC')}], 'C', 'meta',
     [['!'], ['Teeeest', 'nope']], [{'meta': ['C']}, {'meta': ['C', 'C']}]),
])
def test_filter_tokens(docs, docs_meta, search_patterns, by_meta, expected_docs, expected_docs_meta):
    # very simple test here
    # more tests are done via TMPreproc

    res = filter_tokens(docs, search_patterns, docs_meta, by_meta=by_meta)

    if docs_meta is None:
        res_docs = res
        res_docs_meta = None
    else:
        assert isinstance(res, tuple)
        assert len(res) == 2
        res_docs, res_docs_meta = res

    assert res_docs == expected_docs
    assert res_docs_meta == expected_docs_meta


@given(docs=strategy_tokens(string.printable), search_term_exists=st.booleans(), context_size=st.integers(0, 5),
       invert=st.booleans())
def test_filter_tokens_with_kwic(docs, search_term_exists, context_size, invert):
    vocab = list(vocabulary(docs) - {''})

    if search_term_exists and len(vocab) > 0:
        s = random.choice(vocab)
    else:
        s = 'thisdoesnotexist'

    res = filter_tokens_with_kwic(docs, s, context_size=context_size, inverse=invert)
    res_filter_tokens = filter_tokens(docs, s, inverse=invert)
    res_kwic = kwic(docs, s, context_size=context_size, inverse=invert)

    assert isinstance(res, list)
    assert len(res) == len(docs) == len(res_filter_tokens) == len(res_kwic)

    for d, d_, d_ft, d_kwic in zip(docs, res, res_filter_tokens, res_kwic):
        assert isinstance(d_, list)
        assert len(d_) <= len(d)

        if context_size == 0:
            assert d_ == d_ft
        else:
            assert all([t in d for t in d_])
            assert len(d_kwic) == len(d_ft)

            if len(d_) > 0 and len(vocab) > 0 and not invert:
                assert (s in d_) == search_term_exists

            if not invert:
                d_kwic_flat = flatten_list(d_kwic)
                assert set(d_kwic_flat) == set(d_)


@pytest.mark.parametrize('docs, docs_meta, search_patterns, context_size, invert, expected_docs, expected_docs_meta', [
    ([], None, 'test', 2, False, [], None),
    ([], [], 'test', 2, False, [], []),
    ([[]], None, 'test', 2, False, [[]], None),
    ([], [], 'test', 2, False, [], []),
    ([list('abcdefc'), list('c'), list('cc'), list('abba')], None, 'c', 1, False,
     [list('bcdfc'),   list('c'), list('cc'), list()], None),
    ([list('abcdefc'), list('c'), list('cc'), list('abba')], None, 'c', 1, True,
     [list('ae'),      list(),    list(),     list('abba')], None),
    ([list('abcdefc'), list('c'), list('cc'), list('abba')], None, 'c', 2, False,
     [list('abcdefc'), list('c'), list('cc'), list()], None),
    ([list('abcdexxxfc'), list('c'), list('cc'), list('abba')],
     [{'meta': list('XYXYXZZZYX')}, {'meta': list('X')}, {'meta': list('XX')}, {'meta': list('XYZW')}],
     'c', 2, False,
     [list('abcdexfc'), list('c'), list('cc'), list()],
     [{'meta': list('XYXYXZYX')}, {'meta': list('X')}, {'meta': list('XX')}, {'meta': list()}]),
])
def test_filter_tokens_with_kwic_example(docs, docs_meta, search_patterns, context_size, invert,
                                         expected_docs, expected_docs_meta):
    res = filter_tokens_with_kwic(docs, search_patterns, docs_meta, context_size=context_size, inverse=invert)

    if docs_meta is None:
        res_docs = res
        res_docs_meta = None
    else:
        assert isinstance(res, tuple)
        assert len(res) == 2
        res_docs, res_docs_meta = res

    assert res_docs == expected_docs
    assert res_docs_meta == expected_docs_meta


@given(docs=strategy_tokens(alphabet=string.printable, max_size=20), inverse=st.booleans())
def test_filter_tokens_by_mask(docs, inverse):
    mask = [[random.choice([False, True]) for _ in range(n)] for n in map(len, docs)]

    if random.choice([False, True]):
        docs_meta = [{'meta': ['foo'] * n} for n in map(len, docs)]
    else:
        docs_meta = None

    res = filter_tokens_by_mask(docs, mask, docs_meta, inverse=inverse)

    if docs_meta is not None:
        assert isinstance(res, tuple)
        assert len(res) == 2
        res_docs, res_meta = res
        assert len(res_meta) == len(res_docs)
        assert all([len(dmeta) == len(rmeta) for dmeta, rmeta in zip(docs_meta, res_meta)])
    else:
        res_docs = res

    assert len(res_docs) == len(docs)

    for i, (dtok, dmsk) in enumerate(zip(docs, mask)):
        n = len(dmsk) - sum(dmsk) if inverse else sum(dmsk)
        assert len(res_docs[i]) == n


@pytest.mark.parametrize(
    'docs, docs_meta, doc_labels, search_patterns, by_meta, expected_docs, expected_docs_meta, expected_doc_labels',
    [
        ([], None, None, 'test', None, [], None, None),
        ([], [], None, 'test', None, [], [], None),
        ([[]], None, None, 'test', None, [], None, None),
        ([['A', 'test', '!'], ['Teeeest', 'nope']], None, None, 'test', None, [['A', 'test', '!']], None, None),
        ([['A', 'test', '!'], ['Teeeest', 'nope']], [{'meta': list('ABC')}, {'meta': list('CC')}], None, 'test', None,
         [['A', 'test', '!']], [{'meta': list('ABC')}], None),
        ([['A', 'test', '!'], ['Teeeest', 'nope']], None, [['doc1'], ['doc2']], 'test', None,
         [['A', 'test', '!']], None, [['doc1']]),
        ([['A', 'test', '!'], ['Teeeest', 'nope']], [{'meta': list('ABC')}, {'meta': list('CC')}], [['doc1'], ['doc2']],
         'test', None,
         [['A', 'test', '!']], [{'meta': list('ABC')}], [['doc1']]),
        ([['A', 'test', '!'], ['Teeeest', 'nope']], [{'meta': list('ABA')}, {'meta': list('CC')}], [['doc1'], ['doc2']],
         'C', 'meta',
         [['Teeeest', 'nope']], [{'meta': list('CC')}], [['doc2']]),
    ]
)
def test_filter_documents(docs, docs_meta, doc_labels, search_patterns, by_meta,
                          expected_docs, expected_docs_meta, expected_doc_labels):
    # very simple test here
    # more tests are done via TMPreproc

    res = filter_documents(docs, search_patterns, by_meta=by_meta, docs_meta=docs_meta, doc_labels=doc_labels)

    res_docs_meta = None
    res_doc_labels = None

    if docs_meta is None and doc_labels is None:
        res_docs = res
    else:
        assert isinstance(res, tuple)
        assert len(res) == 1 + sum([docs_meta is not None, doc_labels is not None])

        if len(res) == 2:
            if docs_meta is not None:
                res_docs, res_docs_meta = res
            else:
                res_docs, res_doc_labels = res
        else:
            assert len(res) == 3
            res_docs, res_docs_meta, res_doc_labels = res

    assert res_docs == expected_docs
    assert res_docs_meta == expected_docs_meta
    assert res_doc_labels == expected_doc_labels


@pytest.mark.parametrize(
    'docs, doc_labels, docs_meta, name_patterns, expected_docs, expected_doc_labels, expected_docs_meta',
    [
        ([], [], None, 'test', [], [], None),
        ([[]], ['doc1'], None, 'test', [], [], None),
        ([[]], ['test'], None, 'test', [[]], ['test'], None),
        ([['t1', 't2'], ['foo']], ['test', 'bar'], None, 'test', [['t1', 't2']], ['test'], None),
        ([['t1', 't2'], ['foo']], ['test', 'bar'], [{'meta': ['A', 'B']}, {'meta': ['C']}], 'test',
         [['t1', 't2']], ['test'], [{'meta': ['A', 'B']}]),
    ]
)
def test_filter_documents_by_name(docs, doc_labels, docs_meta, name_patterns,
                                  expected_docs, expected_doc_labels, expected_docs_meta):
    # very simple test here
    # more tests are done via TMPreproc

    res = filter_documents_by_name(docs, doc_labels, docs_meta=docs_meta, name_patterns=name_patterns)

    if docs_meta is not None:
        res_docs, res_doc_labels, res_docs_meta = res
    else:
        res_docs, res_doc_labels = res
        res_docs_meta = None

    assert res_docs == expected_docs
    assert res_doc_labels == expected_doc_labels
    assert res_docs_meta == expected_docs_meta


@pytest.mark.parametrize(
    'docs, docs_meta, required_pos, expected_docs, expected_docs_meta',
    [
        ([], [], 'test', [], []),
        ([[]], [{'meta_pos': []}], 'test', [[]], [{'meta_pos': []}]),
        ([['t1', 't2'], ['foo']], [{'meta_pos': ['A', 'B']}, {'meta_pos': ['A']}], 'A',
         [['t1'], ['foo']], [{'meta_pos': ['A']}, {'meta_pos': ['A']}]),
        ([[]], [[]], 'test', [[]], [[]]),
        ([['t1', 't2'], ['foo']], [['A', 'B'], ['A']], 'A',
         [['t1'], ['foo']], [['A'], ['A']]),
    ]
)
def test_filter_for_pos(docs, docs_meta, required_pos, expected_docs, expected_docs_meta):
    # very simple test here
    # more tests are done via TMPreproc

    res_docs, res_docs_meta = filter_for_pos(docs, docs_meta, required_pos=required_pos, simplify_pos=False)

    assert res_docs == expected_docs
    assert res_docs_meta == expected_docs_meta


@pytest.mark.parametrize(
    'docs, docs_meta, common, thresh, absolute, expected_docs, expected_docs_meta',
    [
        ([], None, True, 0.75, False, [], None),
        ([[]], None, True, 0.75, False, [[]], None),
        ([['a']] * 10, None, True, 0.9, False, [[]] * 10, None),
        ([['a']] * 9 + [['b']], None, True, 0.9, False, [[]] * 9 + [['b']], None),
        ([['a']] * 9 + [['b']], [{'meta': ['A']}] * 9 + [{'meta': ['B']}], True, 0.9, False,
         [[]] * 9 + [['b']], [{'meta': []}] * 9 + [{'meta': ['B']}]),
        ([['a']] * 9 + [['b']], None, False, 1, True, [['a']] * 9 + [[]], None),
    ]
)
def test_remove_common_uncommon_tokens(docs, docs_meta, common, thresh, absolute, expected_docs, expected_docs_meta):
    # very simple test here
    # more tests are done via TMPreproc
    if common:
        fn = remove_common_tokens
    else:
        fn = remove_uncommon_tokens

    res = fn(docs, docs_meta, df_threshold=thresh, absolute=absolute)

    if docs_meta is None:
        res_docs = res
        res_docs_meta = None
    else:
        res_docs, res_docs_meta = res

    assert res_docs == expected_docs
    assert res_docs_meta == expected_docs_meta


@given(docs=strategy_tokens(string.printable))
def test_transform(docs):
    expected = [[t.lower() for t in d] for d in docs]

    res1 = transform(docs, str.lower)
    res2 = to_lowercase(docs)

    assert res1 == res2 == expected

    def repeat_token(t, k):
        return t * k

    res = transform(docs, repeat_token, k=3)

    assert len(res) == len(docs)
    for dtok_, dtok in zip(res, docs):
        assert len(dtok_) == len(dtok)
        for t_, t in zip(dtok_, dtok):
            assert len(t_) == 3 * len(t)


def test_simplified_pos():
    assert simplified_pos('') == ''
    assert simplified_pos('N') == 'N'
    assert simplified_pos('V') == 'V'
    assert simplified_pos('ADJ') == 'ADJ'
    assert simplified_pos('ADV') == 'ADV'
    assert simplified_pos('AD') == ''
    assert simplified_pos('ADX') == ''
    assert simplified_pos('PRP') == ''
    assert simplified_pos('XYZ') == ''
    assert simplified_pos('NN') == 'N'
    assert simplified_pos('NNP') == 'N'
    assert simplified_pos('VX') == 'V'
    assert simplified_pos('ADJY') == 'ADJ'
    assert simplified_pos('ADVZ') == 'ADV'

    assert simplified_pos('NNP', tagset='penn') == 'N'
    assert simplified_pos('VFOO', tagset='penn') == 'V'
    assert simplified_pos('JJ', tagset='penn') == 'ADJ'
    assert simplified_pos('JJX', tagset='penn') == 'ADJ'
    assert simplified_pos('RB', tagset='penn') == 'ADV'
    assert simplified_pos('RBFOO', tagset='penn') == 'ADV'
    assert simplified_pos('FOOBAR', tagset='penn') == ''


def test_tokens2ids_lists():
    tok = [list('ABC'), list('ACAB'), list('DEA')]  # tokens2ids converts those to numpy arrays

    vocab, tokids = tokens2ids(tok)

    assert isinstance(vocab, np.ndarray)
    assert np.array_equal(vocab, np.array(list('ABCDE')))
    assert len(tokids) == 3
    assert isinstance(tokids[0], np.ndarray)
    assert np.array_equal(tokids[0], np.array([0, 1, 2]))
    assert np.array_equal(tokids[1], np.array([0, 2, 0, 1]))
    assert np.array_equal(tokids[2], np.array([3, 4, 0]))


def test_tokens2ids_nparrays():
    tok = [list('ABC'), list('ACAB'), list('DEA')]  # tokens2ids converts those to numpy arrays
    tok = list(map(np.array, tok))

    vocab, tokids = tokens2ids(tok)

    assert isinstance(vocab, np.ndarray)
    assert np.array_equal(vocab, np.array(list('ABCDE')))
    assert len(tokids) == 3
    assert isinstance(tokids[0], np.ndarray)
    assert np.array_equal(tokids[0], np.array([0, 1, 2]))
    assert np.array_equal(tokids[1], np.array([0, 2, 0, 1]))
    assert np.array_equal(tokids[2], np.array([3, 4, 0]))


@given(
    tok=st.lists(st.integers(0, 100), min_size=2, max_size=2).flatmap(
        lambda size: st.lists(st.lists(st.text(), min_size=0, max_size=size[0]),
                                       min_size=0, max_size=size[1])
    )
)
def test_tokens2ids_and_ids2tokens(tok):
    tok = list(map(lambda x: np.array(x, dtype=np.str), tok))

    vocab, tokids = tokens2ids(tok)

    assert isinstance(vocab, np.ndarray)
    if tok:
        assert np.array_equal(vocab, np.unique(np.concatenate(tok)))
    else:
        assert np.array_equal(vocab, np.array([], dtype=np.str))

    assert len(tokids) == len(tok)

    tok2 = ids2tokens(vocab, tokids)
    assert len(tok2) == len(tok)

    for orig_tok, tokid, inversed_tokid_tok in zip(tok, tokids, tok2):
        assert isinstance(tokid, np.ndarray)
        assert len(tokid) == len(orig_tok)
        assert np.array_equal(orig_tok, inversed_tokid_tok)


def test_pos_tag_convert_penn_to_wn():
    assert pos_tag_convert_penn_to_wn('JJ') == wn.ADJ
    assert pos_tag_convert_penn_to_wn('RB') == wn.ADV
    assert pos_tag_convert_penn_to_wn('NN') == wn.NOUN
    assert pos_tag_convert_penn_to_wn('VB') == wn.VERB

    for tag in ('', 'invalid', None):
        assert pos_tag_convert_penn_to_wn(tag) is None


def test_str_multisplit():
    punct = list(string.punctuation)

    assert str_multisplit('Te;s,t', {';', ','}) == ['Te', 's', 't']
    assert str_multisplit('US-Student', punct) == ['US', 'Student']
    assert str_multisplit('-main_file.exe,', punct) == ['', 'main', 'file', 'exe', '']


@given(s=st.text(), split_chars=st.lists(st.characters()))
def test_str_multisplit_hypothesis(s, split_chars):
    res = str_multisplit(s, split_chars)

    assert type(res) is list

    if len(s) == 0:
        assert res == ['']

    if len(split_chars) == 0:
        assert res == [s]

    for p in res:
        assert all(c not in p for c in split_chars)

    n_asserted_parts = 0
    for c in set(split_chars):
        n_asserted_parts += s.count(c)
    assert len(res) == n_asserted_parts + 1


def test_str_shape():
    assert str_shape('') == []
    assert str_shape('xxx') == [0, 0, 0]
    assert str_shape('Xxx') == [1, 0, 0]
    assert str_shape('xxX') == [0, 0, 1]
    assert str_shape('Xxx', lower=1, upper=0) == [0, 1, 1]
    assert str_shape('Xxx', lower=1, upper=0, as_str=True) == '011'
    assert str_shape('Foo', lower='x', upper='X', as_str=True) == 'Xxx'


@given(s=st.text(), lower_int=st.integers(min_value=0, max_value=9), upper_int=st.integers(min_value=0, max_value=9),
       lower=st.characters(), upper=st.characters(),
       as_str=st.booleans(), use_ints=st.booleans())
def test_str_shape_hypothesis(s, lower_int, upper_int, lower, upper, as_str, use_ints):
    if use_ints:
        l = lower_int
        u = upper_int
    else:
        l = lower
        u = upper

    res = str_shape(s, l, u, as_str)

    if as_str:
        assert isinstance(res, str)
        assert all([x in {str(l), str(u)} for x in res])
    else:
        assert isinstance(res, list)
        assert all([x in {l, u} for x in res])

    assert len(s) == len(res)


def test_str_shapesplit():
    assert str_shapesplit('') == ['']
    assert str_shapesplit('NewYork') == ['New', 'York']
    assert str_shapesplit('newYork') == ['new', 'York']
    assert str_shapesplit('newyork') == ['newyork']
    assert str_shapesplit('USflag') == ['US', 'flag']
    assert str_shapesplit('eMail') == ['eMail']
    assert str_shapesplit('foobaR') == ['foobaR']


@given(s=st.text(string.printable), precalc_shape=st.booleans(), min_len=st.integers(min_value=1, max_value=5))
def test_str_shapesplit_hypothesis(s, precalc_shape, min_len):
    if precalc_shape:
        shape = str_shape(s)
    else:
        shape = None

    res = str_shapesplit(s, shape, min_part_length=min_len)

    assert len(res) >= 1
    assert all([isinstance(x, str) for x in res])
    if len(s) >= min_len:
        assert all([min_len <= len(x) <= len(s) for x in res])
    assert ''.join(res) == s


def test_expand_compound_token():
    assert expand_compound_token('US-Student') == ['US', 'Student']
    assert expand_compound_token('US-Student-X') == ['US', 'StudentX']
    assert expand_compound_token('Camel-CamelCase') == ['Camel', 'CamelCase']
    assert expand_compound_token('Camel-CamelCase', split_on_casechange=True) == ['Camel', 'Camel', 'Case']
    assert expand_compound_token('Camel-camelCase') == ['Camel', 'camelCase']
    assert expand_compound_token('Camel-camelCase', split_on_casechange=True) == ['Camel', 'camel', 'Case']
    assert expand_compound_token('Student-X') == ['StudentX']
    assert expand_compound_token('Do-Not-Disturb') == ['Do', 'Not', 'Disturb']
    assert expand_compound_token('E-Mobility-Strategy') == ['EMobility', 'Strategy']

    for inp, expected in zip(['US-Student', 'Do-Not-Disturb', 'E-Mobility-Strategy'],
                             [['USStudent'], ['Do', 'Not', 'Disturb'], ['EMobility', 'Strategy']]):
        assert expand_compound_token(inp, split_on_len=None, split_on_casechange=True) == expected

    for inp, expected in zip(['US-Student', 'Do-Not-Disturb', 'E-Mobility-Strategy'],
                             [['US', 'Student'], ['Do', 'Not', 'Disturb'], ['EMobility', 'Strategy']]):
        assert expand_compound_token(inp, split_on_len=2, split_on_casechange=True) == expected

    assert expand_compound_token('E-Mobility-Strategy', split_on_len=1) == ['E', 'Mobility', 'Strategy']

    assert expand_compound_token('') == ['']

    assert expand_compound_token('Te;s,t', split_chars=[';', ','], split_on_len=1, split_on_casechange=False) \
           == expand_compound_token('Te-s-t', split_chars=['-'], split_on_len=1, split_on_casechange=False) \
           == ['Te', 's', 't']


@given(s=st.text(string.printable), split_chars=st.lists(st.characters(min_codepoint=32)),
       split_on_len=st.integers(1),
       split_on_casechange=st.booleans())
def test_expand_compound_token_hypothesis(s, split_chars, split_on_len, split_on_casechange):
    res = expand_compound_token(s, split_chars, split_on_len=split_on_len, split_on_casechange=split_on_casechange)

    assert isinstance(res, list)
    assert len(res) > 0

    s_contains_split_char = any(c in s for c in split_chars)
    s_is_split_chars = all(c in split_chars for c in s)

    if not s_contains_split_char:   # nothing to split on
        assert res == [s]

    if len(s) > 0:
        assert all([p for p in res])

    if not s_is_split_chars:
        for p in res:
            assert all(c not in p for c in split_chars)


@given(docs=strategy_tokens(), chars=st.lists(st.characters()))
def test_remove_chars(docs, chars):
    if len(chars) == 0:
        with pytest.raises(ValueError):
            remove_chars(docs, chars)
    else:
        docs_ = remove_chars(docs, chars)
        assert len(docs_) == len(docs)

        for d_, d in zip(docs_, docs):
            assert len(d_) == len(d)

            for t_, t in zip(d_, d):
                assert len(t_) <= len(t)
                assert all(c not in t_ for c in chars)


def test_remove_chars_example(tokens_mini):
    res = remove_chars(tokens_mini, ['.', ','])
    expected = [
        ['I', 'live', 'in', 'New', 'York', ''],
        ['I', 'am', 'in', 'Berlin', '', 'but', 'my', 'flat', 'is', 'in', 'Munich', ''],
        []
    ]

    assert isinstance(res, list)
    assert len(res) == len(tokens_mini)
    for d_, d, d_exp in zip(res, tokens_mini, expected):
        d_ = _filtered_doc_tokens(d_)
        d = _filtered_doc_tokens(d)
        assert len(d_) == len(d)
        assert d_ == d_exp


@given(matches=st.lists(st.booleans()),
       left=st.integers(min_value=0, max_value=10),
       right=st.integers(min_value=0, max_value=10),
       remove_overlaps=st.booleans())
def test_make_index_window_around_matches_flatten(matches, left, right, remove_overlaps):
    matches = np.array(matches, dtype=np.bool)
    matches_ind = np.where(matches)[0]
    n_true = matches.sum()

    res = make_index_window_around_matches(matches, left, right, flatten=True, remove_overlaps=remove_overlaps)
    assert isinstance(res, np.ndarray)
    assert np.issubdtype(res.dtype, np.int)

    assert len(res) >= n_true

    if len(res) > 0:
        assert np.min(res) >= 0
        assert np.max(res) < len(matches)

    if left == 0 and right == 0:
        assert np.array_equal(matches_ind, res)

    if remove_overlaps:
        assert np.array_equal(res, np.sort(np.unique(res)))

    for i in matches_ind:
        for x in range(i-left, i+right+1):
            if 0 <= x < len(matches):
                assert x in res


@given(matches=st.lists(st.booleans()),
       left=st.integers(min_value=0, max_value=10),
       right=st.integers(min_value=0, max_value=10))
def test_make_index_window_around_matches_not_flattened(matches, left, right):
    matches = np.array(matches, dtype=np.bool)
    matches_ind = np.where(matches)[0]
    n_true = matches.sum()

    res = make_index_window_around_matches(matches, left, right, flatten=False)
    assert isinstance(res, list)
    assert len(res) == n_true == len(matches_ind)

    for win, i in zip(res, matches_ind):
        assert np.issubdtype(win.dtype, np.int)
        assert len(win) > 0
        assert np.min(win) >= 0
        assert np.max(win) < len(matches)

        i_in_win = 0
        for x in range(i-left, i+right+1):
            if 0 <= x < len(matches):
                assert x == win[i_in_win]
                i_in_win += 1


@pytest.mark.parametrize('pattern, tokens, match_type, ignore_case, glob_method, expected', [
    ('a', [], 'exact', False, 'match', []),
    ('', [], 'exact', False, 'match', []),
    ('', ['a', ''], 'exact', False, 'match', [False, True]),
    ('a', ['a', 'b', 'c'], 'exact', False, 'match', [True, False, False]),
    ('a', np.array(['a', 'b', 'c']), 'exact', False, 'match', [True, False, False]),
    ('A', ['a', 'b', 'c'], 'exact', False, 'match', [False, False, False]),
    ('A', ['a', 'b', 'c'], 'exact', True, 'match', [True, False, False]),
    (r'foo$', ['a', 'bfoo', 'c'], 'regex', False, 'match', [False, True, False]),
    (r'foo$', ['a', 'bFOO', 'c'], 'regex', False, 'match', [False, False, False]),
    (r'foo$', ['a', 'bFOO', 'c'], 'regex', True, 'match', [False, True, False]),
    (r'foo*', ['a', 'food', 'c'], 'glob', False, 'match', [False, True, False]),
    (r'foo*', ['a', 'FOOd', 'c'], 'glob', False, 'match', [False, False, False]),
    (r'foo*', ['a', 'FOOd', 'c'], 'glob', True, 'match', [False, True, False]),
    (r'foo*', ['a', 'FOOd', 'c'], 'glob', True, 'search', [False, True, False]),
])
def test_token_match(pattern, tokens, match_type, ignore_case, glob_method, expected):
    assert np.array_equal(token_match(pattern, tokens, match_type, ignore_case, glob_method), np.array(expected))


def test_token_match_subsequent():
    tok = ['green', 'test', 'emob', 'test', 'greener', 'tests', 'test', 'test']

    with pytest.raises(ValueError):
        token_match_subsequent('pattern', tok)

    with pytest.raises(ValueError):
        token_match_subsequent(['pattern'], tok)

    assert token_match_subsequent(['a', 'b'], []) == []

    assert token_match_subsequent(['foo', 'bar'], tok) == []

    res = token_match_subsequent(['green*', 'test*'], tok, match_type='glob')
    assert len(res) == 2
    assert np.array_equal(res[0], np.array([0, 1]))
    assert np.array_equal(res[1], np.array([4, 5]))

    res = token_match_subsequent(['green*', 'test*', '*'], tok, match_type='glob')
    assert len(res) == 2
    assert np.array_equal(res[0], np.array([0, 1, 2]))
    assert np.array_equal(res[1], np.array([4, 5, 6]))


@given(tokens=st.lists(st.text()), n_patterns=st.integers(0, 4))
def test_token_match_subsequent_hypothesis(tokens, n_patterns):
    tokens = np.array(tokens)

    n_patterns = min(len(tokens), n_patterns)

    pat_ind = np.arange(n_patterns)
    np.random.shuffle(pat_ind)
    patterns = list(tokens[pat_ind])

    if len(patterns) < 2:
        with pytest.raises(ValueError):
            token_match_subsequent(patterns, tokens)
    else:
        res = token_match_subsequent(patterns, tokens)

        assert isinstance(res, list)
        if len(tokens) == 0:
            assert res == []
        else:
            for ind in res:
                assert len(ind) == len(patterns)
                assert np.all(ind >= 0)
                assert np.all(ind < len(tokens))
                assert np.all(np.diff(ind) == 1)   # subsequent words
                assert np.array_equal(tokens[ind], patterns)


def test_token_glue_subsequent():
    tok = ['green', 'test', 'emob', 'test', 'greener', 'tests', 'test', 'test']

    with pytest.raises(ValueError):
        token_glue_subsequent(tok, 'invalid')

    assert token_glue_subsequent(tok, []) == tok

    matches = token_match_subsequent(['green*', 'test*'], tok, match_type='glob')
    assert token_glue_subsequent(tok, matches) == ['green_test', 'emob', 'test', 'greener_tests', 'test', 'test']

    matches = token_match_subsequent(['green*', 'test*', '*'], tok, match_type='glob')
    assert token_glue_subsequent(tok, matches) == ['green_test_emob', 'test', 'greener_tests_test', 'test']


@given(tokens=st.lists(st.text(string.printable)), n_patterns=st.integers(0, 4))
def test_token_glue_subsequent_hypothesis(tokens, n_patterns):
    tokens_arr = np.array(tokens)

    n_patterns = min(len(tokens), n_patterns)

    pat_ind = np.arange(n_patterns)
    np.random.shuffle(pat_ind)
    patterns = list(tokens_arr[pat_ind])

    if len(patterns) > 1:
        matches = token_match_subsequent(patterns, tokens)
        assert token_glue_subsequent(tokens, []) == tokens

        if len(tokens) == 0:
            assert token_glue_subsequent(tokens, matches) == []
        elif len(matches) == 0:
            assert token_glue_subsequent(tokens, matches) == tokens
        else:
            res = token_glue_subsequent(tokens, matches)
            assert isinstance(res, list)
            assert 0 < len(res) < len(tokens)

            for ind in matches:
                assert '_'.join(tokens_arr[ind]) in res


@pytest.mark.parametrize(
    'language, docs, expected',
    [
        ('english', [], []),
        ('english', [['A', 'simple', 'example', '.'], ['Simply', 'written', 'documents']],
                    [['DT', 'JJ', 'NN', '.'], ['NNP', 'VBN', 'NNS']]),
        ('german', [['Ein', 'einfaches', 'Beispiel', 'in', 'einfachem', 'Deutsch', '.'],
                    ['Die', 'Dokumente', 'sind', 'sehr', 'kurz', '.']],
                    [['ART', 'ADJA', 'NN', 'APPR', 'ADJA', 'NN', '$.'],
                     ['ART', 'NN', 'VAFIN', 'ADV', 'ADJD', '$.']]),
    ]
)
def test_pos_tag(language, docs, expected):
    tagged_docs = pos_tag(docs, language, doc_meta_key='meta_pos')
    assert len(tagged_docs) == len(docs)

    for tdoc, exp_tags in zip(tagged_docs, expected):
        assert isinstance(tdoc, dict)
        assert tdoc['meta_pos'] == exp_tags

    tagged_docs = pos_tag(docs, language)
    assert len(tagged_docs) == len(docs)

    for tdoc, exp_tags in zip(tagged_docs, expected):
        assert isinstance(tdoc, list)
        assert tdoc == exp_tags


@pytest.mark.parametrize(
    'language, docs, expected',
    [
        ('english', [], []),
        ('english', [['A', 'simple', 'example', '.'], ['Simply', 'written', 'documents']],
                    [['A', 'simple', 'example', '.'], ['Simply', 'write', 'document']]),
        ('german', [['Ein', 'einfaches', 'Beispiel', 'in', 'einfachem', 'Deutsch', '.'],
                    ['Die', 'Dokumente', 'sind', 'sehr', 'kurz', '.']],
                    [['Ein', 'einfach', 'Beispiel', 'in', 'einfach', 'Deutsch', '.'],
                     ['Die', 'Dokument', 'sein', 'sehr', 'kurz', '.']]),
    ]
)
def test_lemmatize(language, docs, expected):
    docs_meta = pos_tag(docs, language)
    lemmata = lemmatize(docs, docs_meta, language)

    assert len(lemmata) == len(docs)

    for lem_tok, expected_tok in zip(lemmata, expected):
        assert isinstance(lem_tok, list)
        assert lem_tok == expected_tok


#%% helper functions


def _init_lang(code):
    """
    Helper function to load spaCy language model for language `code`. If `code` is None, reset (i.e. "unload"
    language model).

    Using this instead of pytest fixtures because the language model is only loaded when necessary, which speeds
    up tests.
    """
    from tmtoolkit.preprocess import _common

    if code is None:  # reset
        _common.nlp = None
    elif _common.nlp is None or _common.nlp.lang != code:  # init if necessary
        init_for_language(code)
