"""
Preprocessing: Tests for ._docfuncs submodule.
"""

import math
import random
import string
from collections import Counter

import pytest
from hypothesis import given, strategies as st, settings
import numpy as np
from spacy.tokens import Doc
from scipy.sparse import isspmatrix_coo

from tmtoolkit.utils import empty_chararray
from tmtoolkit._pd_dt_compat import FRAME_TYPE, USE_DT, pd_dt_colnames
from tmtoolkit.preprocess._common import DEFAULT_LANGUAGE_MODELS, load_stopwords
from tmtoolkit.preprocess._docfuncs import (
    init_for_language, tokenize, doc_tokens, doc_lengths, doc_labels, vocabulary, vocabulary_counts, doc_frequencies,
    ngrams, sparse_dtm, kwic, kwic_table, glue_tokens, expand_compounds, clean_tokens, spacydoc_from_tokens,
    tokendocs2spacydocs, compact_documents,
    _filtered_doc_tokens
)
from ._testcorpora import corpora_sm
from ._testtools import strategy_tokens


LANGUAGE_CODES = list(sorted(DEFAULT_LANGUAGE_MODELS.keys()))

CORPUS_MINI = {
    'ny': 'I live in New York.',
    'bln': 'I am in Berlin, but my flat is in Munich.',
    'compounds': 'US-Student is reading an e-mail on eCommerce with CamelCase.',
    'empty': ''
}


@pytest.fixture(scope='module', params=LANGUAGE_CODES)
def nlp_all(request):
    return init_for_language(request.param)


@pytest.fixture()
def tokens_en():
    _init_lang('en')
    return tokenize(corpora_sm['en'])


@pytest.fixture()
def tokens_en_arrays():
    _init_lang('en')
    return doc_tokens(tokenize(corpora_sm['en']))


@pytest.fixture()
def tokens_en_lists():
    _init_lang('en')
    return doc_tokens(tokenize(corpora_sm['en']), to_lists=True)


@pytest.fixture()
def tokens_mini():
    _init_lang('en')
    return tokenize(CORPUS_MINI)


@pytest.fixture()
def tokens_mini_arrays():
    _init_lang('en')

    # deliberately not using doc_tokens() here
    lists = tokenize(CORPUS_MINI, as_spacy_docs=False)
    return [np.array(doc) if doc else empty_chararray() for doc in lists]


@pytest.fixture()
def tokens_mini_lists():
    _init_lang('en')
    return tokenize(CORPUS_MINI, as_spacy_docs=False)


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
    'testcase, docs, as_spacy_docs, doc_labels, doc_labels_fmt',
    [
        (1, [], True, None, None),
        (2, [''], True, None, None),
        (3, ['', ''], True, None, None),
        (4, ['Simple test.'], True, None, None),
        (5, ['Simple test.', 'Here comes another document.'], True, None, None),
        (6, ['Simple test.', 'Here comes another document.'], True, list('ab'), None),
        (7, ['Simple test.', 'Here comes another document.'], True, None, 'foo_{i0}'),
        (8, {'a': 'Simple test.', 'b': 'Here comes another document.'}, True, None, None),
        (9, ['Simple test.'], False, None, None),
        (10, ['Simple test.', 'Here comes another document.'], False, None, None),
        (11, ['Simple test.', 'Here comes another document.'], False, list('ab'), None),
        (12, ['Simple test.', 'Here comes another document.'], False, None, 'foo_{i0}'),
        (13, {'a': 'Simple test.', 'b': 'Here comes another document.'}, False, None, None),
    ]
)
def test_tokenize(testcase, docs, as_spacy_docs, doc_labels, doc_labels_fmt):
    if testcase == 1:
        _init_lang(None)  # no language initialized

        with pytest.raises(ValueError):
            tokenize(docs)

    _init_lang('en')

    kwargs = {'as_spacy_docs': as_spacy_docs}
    if doc_labels is not None:
        kwargs['doc_labels'] = doc_labels
    if doc_labels_fmt is not None:
        kwargs['doc_labels_fmt'] = doc_labels_fmt

    res = tokenize(docs, **kwargs)

    assert isinstance(res, list)
    assert len(res) == len(docs)
    if as_spacy_docs:
        assert all([isinstance(d, Doc) for d in res])
        assert all([isinstance(d._.label, str) for d in res])  # each doc. has a string label
        # Doc object text must be same as raw text
        assert all([d.text == txt for d, txt in zip(res, docs.values() if isinstance(docs, dict) else docs)])
    else:
        assert all([isinstance(d, list) for d in res])
        assert all([all([isinstance(t, str) for t in d]) for d in res if d])
        # token must occur in raw text
        assert all([[t in txt for t in d] for d, txt in zip(res, docs.values() if isinstance(docs, dict) else docs)])

    if testcase == 1:
        assert len(res) == 0
    elif testcase in {2, 3}:
        assert all([len(d) == 0 for d in res])
    elif testcase in {4, 9}:
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
    elif testcase in {10, 11, 12, 13}:
        assert len(res) == 2
    else:
        raise RuntimeError('testcase not covered:', testcase)


@pytest.mark.parametrize(
    'as_spacy_docs', [True, False]
)
def test_tokenize_all_languages(nlp_all, as_spacy_docs):
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

    res = tokenize(corpus, as_spacy_docs=as_spacy_docs)
    assert isinstance(res, list)
    assert len(res) == len(corpus)

    if as_spacy_docs:
        assert all([isinstance(d, Doc) for d in res])
        assert all([isinstance(d._.label, str) for d in res])     # each doc. has a string label

        for doc in res:
            for arrname in ('tokens', 'mask'):
                assert arrname in doc.user_data and isinstance(doc.user_data[arrname], np.ndarray)

            assert np.issubdtype(doc.user_data['mask'].dtype, np.bool_)
            assert np.all(doc.user_data['mask'])
            assert np.issubdtype(doc.user_data['tokens'].dtype, np.unicode_)

        res_dict = {d._.label: d for d in res}
    else:
        assert all([isinstance(d, list) for d in res])
        assert all([all([isinstance(t, str) for t in d]) for d in res if d])

        res_dict = dict(zip(corpus.keys(), res))

    firstdoc_label, firstword = firstwords[nlp_all.lang]
    firstdoc = res_dict[firstdoc_label]
    assert len(firstdoc) > 0

    if as_spacy_docs:
        assert firstdoc[0].text == firstdoc.user_data['tokens'][0] == firstword

        for dl, txt in corpus.items():
            d = res_dict[dl]
            assert d.text == txt
    else:
        assert firstdoc[0] == firstword


def test_doc_tokens(tokens_mini, tokens_mini_arrays, tokens_mini_lists):
    doc_tok_arrays = doc_tokens(tokens_mini)
    doc_tok_lists = doc_tokens(tokens_mini, to_lists=True)
    assert len(doc_tok_arrays) == len(doc_tok_lists) == len(tokens_mini_arrays)
    for tok_arr, tok_list, expected_arr, expected_list in zip(doc_tok_arrays, doc_tok_lists,
                                                              tokens_mini_arrays, tokens_mini_lists):
        assert isinstance(tok_arr, np.ndarray)
        assert np.issubdtype(tok_arr.dtype, np.unicode_)
        assert isinstance(tok_list, list)
        assert np.array_equal(tok_arr, expected_arr)
        assert tok_list == expected_list


def test_doc_lengths(tokens_en, tokens_en_arrays, tokens_en_lists):
    for tokens in (tokens_en, tokens_en_arrays, tokens_en_lists):
        res = doc_lengths(tokens)
        assert isinstance(res, list)
        assert len(res) == len(tokens)

        for n, d in zip(res, tokens):
            assert isinstance(n, int)
            assert n == len(d)
            if tokens is tokens_en and d._.label == 'empty':
                assert n == len(d) == 0


def test_doc_labels(tokens_en, tokens_en_arrays, tokens_en_lists):
    assert set(doc_labels(tokens_en)) == set(corpora_sm['en'])

    _init_lang('en')
    docs = tokenize(['test doc 1', 'test doc 2', 'test doc 3'], doc_labels=list('abc'))
    assert doc_labels(docs) == list('abc')

    with pytest.raises(ValueError):   # require spaCy docs
        doc_labels(tokens_en_arrays)
    with pytest.raises(ValueError):   # require spaCy docs
        doc_labels(tokens_en_lists)


@pytest.mark.parametrize('sort', [False, True])
def test_vocabulary(tokens_en, tokens_en_arrays, tokens_en_lists, sort):
    for tokens in (tokens_en, tokens_en_arrays, tokens_en_lists):
        res = vocabulary(tokens, sort=sort)

        if sort:
            assert isinstance(res, list)
            assert sorted(res) == res
        else:
            assert isinstance(res, set)

        for t in res:
            assert isinstance(t, str)
            assert any([t in dtok for dtok in doc_tokens(tokens)])


def test_vocabulary_counts(tokens_en, tokens_en_arrays, tokens_en_lists):
    for tokens in (tokens_en, tokens_en_arrays, tokens_en_lists):
        res = vocabulary_counts(tokens)
        n_tok = sum(doc_lengths(tokens))

        assert isinstance(res, Counter)
        assert set(res.keys()) == vocabulary(tokens)
        assert all([0 < n <= n_tok for n in res.values()])
        assert any([n > 1 for n in res.values()])


@pytest.mark.parametrize('proportions', [False, True])
def test_doc_frequencies(tokens_en, tokens_en_arrays, tokens_en_lists, proportions):
    for tokens in (tokens_en, tokens_en_arrays, tokens_en_lists):
        res = doc_frequencies(tokens, proportions=proportions)

        assert set(res.keys()) == vocabulary(tokens)

        if proportions:
            assert isinstance(res, dict)
            assert all([0 < v <= 1 for v in res.values()])
        else:
            assert isinstance(res, Counter)
            assert all([0 < v < len(tokens) for v in res.values()])
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


@pytest.mark.parametrize('n', list(range(0, 5)))
def test_ngrams(tokens_en, tokens_en_arrays, tokens_en_lists, n):
    for tokens in (tokens_en, tokens_en_arrays, tokens_en_lists):
        if n < 2:
            with pytest.raises(ValueError):
                ngrams(tokens, n)
        else:
            docs_unigrams = doc_tokens(tokens)
            docs_ng = ngrams(tokens, n, join=False)
            docs_ng_joined = ngrams(tokens, n, join=True, join_str='')
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

                    if tokens is tokens_en_lists:
                        assert tokens_ == tok
                    else:
                        assert tokens_ == tok.tolist()


@pytest.mark.parametrize('pass_vocab', [False, True])
def test_sparse_dtm(tokens_en, tokens_en_arrays, tokens_en_lists, pass_vocab):
    emptydoc_index = [d._.label for d in tokens_en].index('empty')
    for tokens in (tokens_en, tokens_en_arrays, tokens_en_lists):
        if pass_vocab:
            vocab = vocabulary(tokens, sort=True)
            dtm = sparse_dtm(tokens, vocab)
        else:
            dtm, vocab = sparse_dtm(tokens)

        assert isspmatrix_coo(dtm)
        assert dtm.shape == (len(tokens), len(vocab))
        assert vocab == vocabulary(tokens, sort=True)

        doc_ntok = dtm.sum(axis=1)
        assert doc_ntok[emptydoc_index, 0] == 0


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


@given(search_term_exists=st.booleans(), context_size=st.integers(1, 5), as_dict=st.booleans(),
       non_empty=st.booleans(), glue=st.booleans(), highlight_keyword=st.booleans())
def test_kwic(tokens_en, tokens_en_arrays, tokens_en_lists,
              search_term_exists, context_size, as_dict, non_empty, glue, highlight_keyword):
    for tokens in (tokens_en, tokens_en_arrays, tokens_en_lists):
        vocab = list(vocabulary(tokens))

        if search_term_exists and len(vocab) > 0:
            s = random.choice(vocab)
        else:
            s = 'thisdoesnotexist'

        glue_arg = ' ' if glue else None
        highlight_arg = '*' if highlight_keyword else None

        kwic_kwargs = dict(context_size=context_size, non_empty=non_empty, as_dict=as_dict, glue=glue_arg,
                           highlight_keyword=highlight_arg)

        if as_dict and tokens is not tokens_en:
            with pytest.raises(ValueError):
                kwic(tokens, s, **kwic_kwargs)
            return
        else:
            res = kwic(tokens, s, **kwic_kwargs)

        assert isinstance(res, dict if as_dict else list)

        if as_dict:
            if non_empty:
                assert all(k in doc_labels(tokens) for k in res.keys())
            else:
                assert list(res.keys()) == doc_labels(tokens)
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


def test_kwic_example(tokens_mini, tokens_mini_arrays, tokens_mini_lists):
    for tokens in (tokens_mini, tokens_mini_arrays, tokens_mini_lists):
        res = kwic(tokens, 'in', context_size=1)
        assert res == [
            [['live', 'in', 'New']],
            [['am', 'in', 'Berlin'], ['is', 'in', 'Munich']],
            [],
            [],
        ]

        if tokens is tokens_mini:
            res = kwic(tokens, 'in', context_size=1, as_dict=True)
            assert res == {
                'ny': [['live', 'in', 'New']],
                'bln': [['am', 'in', 'Berlin'], ['is', 'in', 'Munich']],
                'compounds': [],
                'empty': [],
            }
        else:
            with pytest.raises(ValueError):
                kwic(tokens, 'in', context_size=1, as_dict=True)

        res = kwic(tokens, 'in', context_size=1, non_empty=True)
        assert res == [
            [['live', 'in', 'New']],
            [['am', 'in', 'Berlin'], ['is', 'in', 'Munich']],
        ]

        res = kwic(tokens, 'in', context_size=1, non_empty=True, glue=' ')
        assert res == [
            ['live in New'],
            ['am in Berlin', 'is in Munich']
        ]

        res = kwic(tokens, 'in', context_size=1, non_empty=True, glue=' ', highlight_keyword='*')
        assert res == [
            ['live *in* New'],
            ['am *in* Berlin', 'is *in* Munich']
        ]

        res = kwic(tokens, 'in', context_size=1, non_empty=True, highlight_keyword='*')
        assert res == [
            [['live', '*in*', 'New']],
            [['am', '*in*', 'Berlin'], ['is', '*in*', 'Munich']],
        ]


@given(search_term_exists=st.booleans(), context_size=st.integers(1, 5))
def test_kwic_table(tokens_en, tokens_en_arrays, tokens_en_lists, context_size, search_term_exists):
    for tokens in (tokens_en, tokens_en_arrays, tokens_en_lists):
        vocab = list(vocabulary(tokens))

        if search_term_exists and len(vocab) > 0:
            s = random.choice(vocab)
        else:
            s = 'thisdoesnotexist'

        if tokens is tokens_en:
            res = kwic_table(tokens, s, context_size=context_size)
        else:
            with pytest.raises(ValueError):
                kwic_table(tokens, s, context_size=context_size)
            return

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

            assert all(dl in doc_labels(tokens) for dl in set(docs_col))

            for kwic_match in kwic_col:
                assert kwic_match.count('*') >= 2
        else:
            assert res.shape[0] == 0


def test_glue_tokens_example(tokens_mini, tokens_mini_arrays, tokens_mini_lists):
    for testtokens in (tokens_mini, tokens_mini_arrays, tokens_mini_lists):
        tokens = doc_tokens(testtokens)
        res = glue_tokens(testtokens, ('New', 'York'))
        tokens_ = doc_tokens(res)
        assert isinstance(res, list)
        assert len(res) == len(testtokens) == len(tokens_)

        if testtokens is tokens_mini:
            assert all([d1 is d2 for d1, d2 in zip(res, testtokens)])   # modifies in-place
        else:
            assert all([d1 is not d2 for d1, d2 in zip(res, testtokens)])  # does *not* modify in-place

        for i, (d_, d) in enumerate(zip(tokens_, tokens)):
            if testtokens is not tokens_mini_lists:
                d = d.tolist()
                if testtokens is tokens_mini:
                    d_ = d_.tolist()

            if i == 0:
                assert d_ == ['I', 'live', 'in', 'New_York', '.']
            else:
                assert d_ == d

        res, glued = glue_tokens(res, ('in', '*'), glue='/', match_type='glob', return_glued_tokens=True)
        tokens_ = doc_tokens(res)

        if testtokens is tokens_mini:
            assert all([d1 is d2 for d1, d2 in zip(res, testtokens)])   # modifies in-place
        else:
            assert all([d1 is not d2 for d1, d2 in zip(res, testtokens)])  # does *not* modify in-place

        assert glued == {'in/New_York', 'in/Berlin', 'in/Munich'}

        for i, (d_, d) in enumerate(zip(tokens_, tokens)):
            if testtokens is not tokens_mini_lists:
                d = d.tolist()
                if testtokens is tokens_mini:
                    d_ = d_.tolist()

            if i == 0:
                assert d_ == ['I', 'live', 'in/New_York', '.']
            elif i == 1:
                assert d_ == ['I', 'am', 'in/Berlin', ',', 'but', 'my', 'flat', 'is', 'in/Munich', '.']
            else:
                assert d_ == d


@pytest.mark.parametrize(
    'testcase, split_chars, split_on_len, split_on_casechange',
    [
        (1, ['-'], 2, False),
        (2, ['-'], 2, True),
        (3, [], 2, True),
    ]
)
def test_expand_compounds(tokens_mini, tokens_mini_arrays, tokens_mini_lists,
                          testcase, split_chars, split_on_len, split_on_casechange):
    for testtokens in (tokens_mini, tokens_mini_arrays, tokens_mini_lists):
        res = expand_compounds(testtokens, split_chars=split_chars, split_on_len=split_on_len,
                               split_on_casechange=split_on_casechange)
        assert isinstance(res, list)
        assert len(res) == len(testtokens)

        got_compounds = False
        for expdoc, origdoc in zip(res, testtokens):
            if len(origdoc) == 0:
                assert len(expdoc) == 0
            else:
                assert len(expdoc) >= len(origdoc)

            if 'CamelCase' in vocabulary([origdoc]):   # doc with compounds
                got_compounds = True
                expdoc_tokens = _filtered_doc_tokens(expdoc, as_list=True)
                if testcase == 1:
                    assert expdoc_tokens == ['US', '-', 'Student', 'is', 'reading', 'an', 'e', '-', 'mail', 'on',
                                             'eCommerce', 'with', 'CamelCase', '.']
                elif testcase in {2, 3}:
                    assert expdoc_tokens == ['US', '-', 'Student', 'is', 'reading', 'an', 'e', '-', 'mail', 'on',
                                             'eCommerce', 'with', 'Camel', 'Case', '.']
            else:  # doc without compounds
                if testtokens is tokens_mini_arrays:
                    assert np.array_equal(expdoc, origdoc)
                else:
                    assert _filtered_doc_tokens(expdoc, as_list=True) == _filtered_doc_tokens(origdoc, as_list=True)

        assert got_compounds


@pytest.mark.parametrize(
    'docs, expected',
    [
        ([], []),
        ([['']], [['']]),
        ([[''], []], [[''], []]),
        ([['An', 'US-Student', '.']], [['An', 'US', 'Student', '.']]),
    ]
)
def test_expand_compounds_examples(docs, expected):
    assert expand_compounds(docs) == expected


@settings(deadline=1000)
@given(docs=strategy_tokens(string.printable),
       docs_type=st.integers(0, 2),
       remove_punct=st.integers(0, 2),
       remove_stopwords=st.integers(0, 2),
       remove_empty=st.booleans(),
       remove_shorter_than=st.integers(-2, 5),
       remove_longer_than=st.integers(-2, 10),
       remove_numbers=st.booleans())
def test_clean_tokens(docs, docs_type, remove_punct, remove_stopwords, remove_empty,
                      remove_shorter_than, remove_longer_than, remove_numbers):
    _init_lang('en')

    docs_as_tokens = doc_tokens(docs, to_lists=True)
    docs_vocab = list(vocabulary(docs_as_tokens))

    if docs_type == 1:     # arrays
        docs = [np.array(d) if d else empty_chararray() for d in docs]
    elif docs_type == 2:   # spaCy docs
        docs = tokendocs2spacydocs(docs)

    if remove_punct == 2:
        remove_punct = np.random.choice(list(string.punctuation), 5, replace=False).tolist()
    else:
        remove_punct = bool(remove_punct)

    if remove_stopwords == 2 and docs_vocab:
        remove_stopwords = np.random.choice(docs_vocab, 5, replace=True).tolist()
    else:
        remove_stopwords = bool(remove_stopwords)

    if remove_shorter_than == -2:
        remove_shorter_than = None

    if remove_longer_than == -2:
        remove_longer_than = None

    if remove_shorter_than == -1 or remove_longer_than == -1:
        with pytest.raises(ValueError):
            clean_tokens(docs, remove_punct=remove_punct, remove_stopwords=remove_stopwords,
                         remove_empty=remove_empty, remove_shorter_than=remove_shorter_than,
                         remove_longer_than=remove_longer_than, remove_numbers=remove_numbers)
    else:
        docs_ = clean_tokens(docs, remove_punct=remove_punct, remove_stopwords=remove_stopwords,
                             remove_empty=remove_empty, remove_shorter_than=remove_shorter_than,
                             remove_longer_than=remove_longer_than, remove_numbers=remove_numbers)

        if docs_type == 2:
            docs_ = compact_documents(docs_)

        blacklist = set()

        if isinstance(remove_punct, list):
            blacklist.update(remove_punct)
        elif remove_punct is True:
            blacklist.update(string.punctuation)

        if isinstance(remove_stopwords, list):
            blacklist.update(remove_stopwords)
        elif remove_stopwords is True:
            blacklist.update(load_stopwords('en'))

        if remove_empty:
            blacklist.update('')

        assert len(docs) == len(docs_)

        for i, (dtok, dtok_) in enumerate(zip(docs, docs_)):
            assert len(dtok) >= len(dtok_)
            del dtok

            assert all([w not in _filtered_doc_tokens(dtok_) for w in blacklist])

            tok_lengths = np.array(list(map(len, dtok_)))

            if remove_shorter_than is not None:
                assert np.all(tok_lengths >= remove_shorter_than)

            if remove_longer_than is not None:
                assert np.all(tok_lengths <= remove_longer_than)

            if remove_numbers and len(dtok_) > 0:
                assert not np.any(np.char.isnumeric(dtok_))


@given(
    pass_vocab=st.integers(min_value=0, max_value=2),
    pass_spaces=st.integers(min_value=0, max_value=3),
    pass_lemmata=st.integers(min_value=0, max_value=3),
    pass_label=st.booleans()
)
def test_spacydoc_from_tokens(tokens_en_arrays, tokens_en_lists, pass_vocab, pass_spaces, pass_lemmata, pass_label):
    for testtokens in (tokens_en_arrays, tokens_en_lists):
        for tokdoc in testtokens:
            should_raise = False
            tokdoc_list = tokdoc.tolist() if testtokens is tokens_en_arrays else tokdoc

            if pass_vocab:
                vocab = vocabulary([tokdoc_list])
                if pass_vocab == 2:
                    vocab = np.array(vocab) if vocab else empty_chararray()
            else:
                vocab = None

            if pass_spaces:
                if pass_spaces == 1:
                    spaces = [' '] * len(tokdoc)
                elif pass_spaces == 2:
                    spaces = np.repeat(' ', len(tokdoc)) if len(tokdoc) > 0 else empty_chararray()
                elif pass_spaces == 3:
                    spaces = [' '] * (len(tokdoc) + 1)   # wrong number of spaces
                    should_raise = True
                else:
                    pytest.fail('invalid value for pass_spaces: %d' % pass_spaces)
                    return
            else:
                spaces = None

            if pass_lemmata:
                if pass_lemmata == 1:
                    lemmata = tokdoc_list
                elif pass_lemmata == 2:
                    lemmata = np.array(tokdoc) if len(tokdoc) > 0 else empty_chararray()
                elif pass_lemmata == 3:
                    lemmata = tokdoc_list + ['foo']   # wrong number of lemmata
                    should_raise = True
                else:
                    pytest.fail('invalid value for pass_lemmata: %d' % pass_lemmata)
                    return
            else:
                lemmata = None

            if pass_label:
                label = 'testdoc'
            else:
                label = None

            if should_raise:
                with pytest.raises(ValueError):
                    spacydoc_from_tokens(tokdoc, vocab=vocab, spaces=spaces, lemmata=lemmata, label=label)
                return
            else:
                doc = spacydoc_from_tokens(tokdoc, vocab=vocab, spaces=spaces, lemmata=lemmata, label=label)
                assert isinstance(doc, Doc)
                assert len(doc) == len(tokdoc)
                assert all([t.text == t_ for t, t_ in zip(doc, tokdoc)])
                assert np.array_equal(doc.user_data['tokens'], tokdoc if len(tokdoc) > 0 else empty_chararray())
                assert np.array_equal(doc.user_data['mask'], np.repeat(True, len(tokdoc)))

                if pass_lemmata:
                    assert all([t.lemma_ == t_ for t, t_ in zip(doc, tokdoc)])


#%% helper functions

_nlp_instances_cache = {}

def _init_lang(code):
    """
    Helper function to load spaCy language model for language `code`. If `code` is None, reset (i.e. "unload"
    language model).

    Using this instead of pytest fixtures because the language model is only loaded when necessary, which speeds
    up tests.
    """
    from tmtoolkit.preprocess import _docfuncs

    if code is None:  # reset
        _docfuncs.nlp = None
    elif _docfuncs.nlp is None or _docfuncs.nlp.lang != code:  # init if necessary
        if code in _nlp_instances_cache.keys():
            _docfuncs.nlp = _nlp_instances_cache[code]
        else:
            init_for_language(code)
