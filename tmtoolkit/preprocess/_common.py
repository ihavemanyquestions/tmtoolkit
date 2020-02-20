"""
Common functions for text processing.

Most functions of this internal module are exported in ``__init__.py`` and make up the functional text processing API of
tmtoolkit.

Markus Konrad <markus.konrad@wzb.eu>
"""

import re
import os
import operator
import pickle
from collections import Counter, OrderedDict
from functools import partial

import globre
import numpy as np
import spacy
from spacy.tokens import Doc

from .._pd_dt_compat import pd_dt_frame, pd_dt_concat, pd_dt_sort
from ..bow.dtm import create_sparse_dtm
from ..utils import flatten_list, require_listlike, empty_chararray, widen_chararray, require_listlike_or_set


MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
DATAPATH = os.path.normpath(os.path.join(MODULE_PATH, '..', 'data'))

DEFAULT_LANGUAGE_MODELS = {
    'en': 'en_core_web_sm',
    'de': 'de_core_news_sm',
    'fr': 'fr_core_news_sm',
    'es': 'es_core_news_sm',
    'pt': 'pt_core_news_sm',
    'it': 'it_core_news_sm',
    'nl': 'nl_core_news_sm',
    'el': 'el_core_news_sm',
    'nb': 'nb_core_news_sm',
    'lt': 'lt_core_news_sm',
}

LANGUAGE_LABELS = {
    'en': 'english',
    'de': 'german',
    'fr': 'french',
    'es': 'spanish',
    'pt': 'portuguese',
    'it': 'italian',
    'nl': 'dutch',
    'el': 'greek',
    'nb': 'norwegian-bokmal',
    'lt': 'lithuanian',
}


#%% global spaCy nlp instance

#: Global spaCy nlp instance which must be initiated via :func:`tmtoolkit.preprocess.init_for_language` when using
#: the functional preprocess API
nlp = None


#%% functions that operate on lists of documents


def init_for_language(language=None, language_model=None, **spacy_opts):
    """
    Initialize the functional API for a given language code `language` or a spaCy language model `language_model`.
    The spaCy nlp instance will be returned and will also be used by default in all subsequent preprocess API calls.

    :param language: two-letter ISO 639-1 language code (lowercase)
    :param language_model: spaCy language model `language_model`
    :param spacy_opts: additional keyword arguments passed to ``spacy.load()``
    :return: spaCy nlp instance
    """
    if language is None and language_model is None:
        raise ValueError('either `language` or `language_model` must be given')

    if language_model is None:
        if not isinstance(language, str) or len(language) != 2:
            raise ValueError('`language` must be a two-letter ISO 639-1 language code')

        if language not in DEFAULT_LANGUAGE_MODELS:
            raise ValueError('language "%s" is not supported' % language)
        language_model = DEFAULT_LANGUAGE_MODELS[language]

    spacy_kwargs = dict(disable=['parser', 'ner'])
    spacy_kwargs.update(spacy_opts)

    global nlp
    nlp = spacy.load(language_model, **spacy_kwargs)

    return nlp


def tokenize(docs, doc_labels=None, doc_labels_fmt='doc-{i1}', nlp_instance=None):
    """
    Tokenize a list or dict of documents `docs`, where each element contains the raw text of the document as string.

    Requires that :func:`~tmtoolkit.preprocess.init_for_language` is called before or `nlp_instance` is passed.

    :param docs: list or dict of documents with raw text strings; if dict, use dict keys as document labels
    :param doc_labels: if not None and `docs` is a list, use strings in this list as document labels
    :param doc_labels_fmt: if `docs` is a list and `doc_labels` is None, generate document labels according to this
                           format, where ``{i0}`` or ``{i1}`` are replaced by the respective zero- or one-indexed
                           document numbers
    :param nlp_instance: spaCy nlp instance
    :return: list of tokenized documents (list of spaCy ``Doc`` objects)
    """

    dictlike = hasattr(docs, 'keys') and hasattr(docs, 'values')

    if not isinstance(docs, (list, tuple)) and not dictlike:
        raise ValueError('`docs` must be a list, tuple or dict-like object')

    if not isinstance(doc_labels_fmt, str):
        raise ValueError('`doc_labels_fmt` must be a string')

    _nlp = _current_nlp(nlp_instance)

    if doc_labels is None:
        if dictlike:
            doc_labels = docs.keys()
            docs = docs.values()
        else:
            doc_labels = [doc_labels_fmt.format(i0=i, i1=i+1) for i in range(len(docs))]
    elif len(doc_labels) != len(docs):
        raise ValueError('`doc_labels` must have same length as `docs`')

    tokenized_docs = [_nlp.make_doc(d) for d in docs]
    del docs

    for dl, doc in zip(doc_labels, tokenized_docs):
        doc._.label = dl
        _init_doc(doc)

    return tokenized_docs


def doc_labels(docs):
    """
    Return list of document labels that are assigned to documents `docs`.

    :param docs: list of tokenized documents
    :return: list of document labels
    """

    require_listlike(docs)
    return [d._.label for d in docs]


def doc_lengths(docs):
    """
    Return document length (number of tokens in doc.) for each document.

    :param docs: list of tokenized documents
    :return: list of document lengths
    """
    require_listlike(docs)

    return list(map(len, docs))


def vocabulary(docs, sort=False):
    """
    Return vocabulary, i.e. set of all tokens that occur at least once in at least one of the documents in `docs`.

    :param docs: list of tokenized documents
    :param sort: return as sorted list
    :return: either set of token strings or sorted list if `sort` is True
    """
    require_listlike(docs)

    v = set(flatten_list(_filtered_docs_tokens(docs)))

    if sort:
        return sorted(v)
    else:
        return v


def vocabulary_counts(docs):
    """
    Return :class:`collections.Counter()` instance of vocabulary containing counts of occurrences of tokens across
    all documents.

    :param docs: list of tokenized documents
    :return: :class:`collections.Counter()` instance of vocabulary containing counts of occurrences of tokens across
             all documents
    """
    require_listlike(docs)

    return Counter(flatten_list(_filtered_docs_tokens(docs)))


def doc_frequencies(docs, proportions=False):
    """
    Document frequency per vocabulary token as dict with token to document frequency mapping.
    Document frequency is the measure of how often a token occurs *at least once* in a document.
    Example with absolute document frequencies:

    .. code-block:: text

        doc tokens
        --- ------
        A   z, z, w, x
        B   y, z, y
        C   z, z, y, z

        document frequency df(z) = 3  (occurs in all 3 documents)
        df(x) = df(w) = 1 (occur only in A)
        df(y) = 2 (occurs in B and C)
        ...

    :param docs: list of tokenized documents
    :param proportions: if True, normalize by number of documents to obtain proportions
    :return: dict mapping token to document frequency
    """
    require_listlike(docs)

    doc_freqs = Counter()

    for dtok in docs:
        for t in set(_filtered_doc_tokens(dtok)):
            doc_freqs[t] += 1

    if proportions:
        n_docs = len(docs)
        return {w: n/n_docs for w, n in doc_freqs.items()}
    else:
        return doc_freqs


def ngrams(docs, n, join=True, join_str=' '):
    """
    Generate and return n-grams of length `n`.

    :param docs: list of tokenized documents
    :param n: length of n-grams, must be >= 2
    :param join: if True, join generated n-grams by string `join_str`
    :param join_str: string used for joining
    :return: list of n-grams; if `join` is True, the list contains strings of joined n-grams, otherwise the list
             contains lists of size `n` in turn containing the strings that make up the n-gram
    """
    require_listlike(docs)

    return [_ngrams_from_tokens(_filtered_doc_tokens(dtok, as_list=True), n=n, join=join, join_str=join_str)
            for dtok in docs]


def sparse_dtm(docs, vocab=None):
    """
    Create a sparse document-term-matrix (DTM) from a list of tokenized documents `docs`. If `vocab` is None, determine
    the vocabulary (unique terms) from `docs`, otherwise take `vocab` which must be a *sorted* list or NumPy array.
    If `vocab` is None, the generated sorted vocabulary list is returned as second value, else only a single value is
    returned -- the DTM.

    :param docs: list of tokenized documents
    :param vocab: optional *sorted* list / NumPy array of vocabulary (unique terms) in `docs`
    :return: either a single value (sparse document-term-matrix) or a tuple with sparse DTM and sorted vocabulary if
             none was passed
    """
    require_listlike(docs)

    if vocab is None:
        vocab = vocabulary(docs, sort=True)
        return_vocab = True
    else:
        return_vocab = False

    docs_tokens = _filtered_docs_tokens(docs)
    alloc_size = sum(len(set(dtok)) for dtok in docs_tokens)  # sum of *unique* tokens in each document

    dtm = create_sparse_dtm(vocab, docs_tokens, alloc_size, vocab_is_sorted=True)

    if return_vocab:
        return dtm, vocab
    else:
        return dtm


def kwic(docs, search_tokens, context_size=2, match_type='exact', ignore_case=False,
         glob_method='match', inverse=False, with_metadata=False, as_dict=False, as_datatable=False, non_empty=False,
         glue=None, highlight_keyword=None):
    """
    Perform keyword-in-context (kwic) search for search pattern(s) `search_tokens`. Returns result as list of KWIC
    windows or datatable / dataframe. If you want to filter with KWIC, use
    :func:`~tmtoolkit.preprocess.filter_tokens_with_kwic`, which returns results as list of tokenized documents (same
    structure as `docs`).

    Uses similar search parameters as :func:`~tmtoolkit.preprocess.filter_tokens`.

    :param docs: list of tokenized documents
    :param search_tokens: single string or list of strings that specify the search pattern(s)
    :param context_size: either scalar int or tuple (left, right) -- number of surrounding words in keyword context.
                         if scalar, then it is a symmetric surrounding, otherwise can be asymmetric.
    :param match_type: the type of matching that is performed: ``'exact'`` does exact string matching (optionally
                       ignoring character case if ``ignore_case=True`` is set); ``'regex'`` treats ``search_tokens``
                       as regular expressions to match the tokens against; ``'glob'`` uses "glob patterns" like
                       ``"politic*"`` which matches for example "politic", "politics" or ""politician" (see
                       `globre package <https://pypi.org/project/globre/>`_)
    :param ignore_case: ignore character case (applies to all three match types)
    :param glob_method: if `match_type` is 'glob', use this glob method. Must be 'match' or 'search' (similar
                        behavior as Python's :func:`re.match` or :func:`re.search`)
    :param inverse: inverse the match results for filtering (i.e. *remove* all tokens that match the search
                    criteria)
    :param with_metadata: also return metadata (like POS) along with each token
    :param as_dict: if True, return result as dict with document labels mapping to KWIC results
    :param as_datatable: return result as data frame with indices "doc" (document label) and "context" (context
                          ID per document) and optionally "position" (original token position in the document) if
                          tokens are not glued via `glue` parameter
    :param non_empty: if True, only return non-empty result documents
    :param glue: if not None, this must be a string which is used to combine all tokens per match to a single string
    :param highlight_keyword: if not None, this must be a string which is used to indicate the start and end of the
                              matched keyword
    :return: return either as: (1) list with KWIC results per document, (2) as dict with document labels mapping to
             KWIC results when `as_dict` is True or (3) dataframe / datatable when `as_datatable` is True
    """
    require_listlike(docs)

    if isinstance(context_size, int):
        context_size = (context_size, context_size)
    else:
        require_listlike(context_size)

    if highlight_keyword is not None and not isinstance(highlight_keyword, str):
        raise ValueError('if `highlight_keyword` is given, it must be of type str')

    if glue:
        if with_metadata or as_datatable:
            raise ValueError('when `glue` is set to True, `with_metadata` and `as_datatable` must be False')
        if not isinstance(glue, str):
            raise ValueError('if `glue` is given, it must be of type str')

    kwic_raw = _build_kwic(docs, search_tokens,
                           highlight_keyword=highlight_keyword,
                           with_metadata=with_metadata,
                           with_window_indices=as_datatable,
                           context_size=context_size,
                           match_type=match_type,
                           ignore_case=ignore_case,
                           glob_method=glob_method,
                           inverse=inverse)

    if as_dict or as_datatable:
        kwic_raw = dict(zip(doc_labels(docs), kwic_raw))

    return _finalize_kwic_results(kwic_raw,
                                  non_empty=non_empty,
                                  glue=glue,
                                  as_datatable=as_datatable,
                                  with_metadata=with_metadata)


def kwic_table(docs, search_tokens, context_size=2, match_type='exact', ignore_case=False,
               glob_method='match', inverse=False, glue=' ', highlight_keyword='*'):
    """
    Shortcut for :func:`~tmtoolkit.preprocess.kwic()` to directly return a data frame table with highlighted keywords
    in context.

    :param docs: list of tokenized documents
    :param search_tokens: single string or list of strings that specify the search pattern(s)
    :param context_size: either scalar int or tuple (left, right) -- number of surrounding words in keyword context.
                         if scalar, then it is a symmetric surrounding, otherwise can be asymmetric.
    :param match_type: One of: 'exact', 'regex', 'glob'. If 'regex', `search_token` must be RE pattern. If `glob`,
                       `search_token` must be a "glob" pattern like "hello w*"
                       (see https://github.com/metagriffin/globre).
    :param ignore_case: If True, ignore case for matching.
    :param glob_method: If `match_type` is 'glob', use this glob method. Must be 'match' or 'search' (similar
                        behavior as Python's :func:`re.match` or :func:`re.search`).
    :param inverse: Invert the matching results.
    :param glue: If not None, this must be a string which is used to combine all tokens per match to a single string
    :param highlight_keyword: If not None, this must be a string which is used to indicate the start and end of the
                              matched keyword.
    :return: datatable or pandas DataFrame with columns "doc" (document label), "context" (context ID per document) and
             "kwic" containing strings with highlighted keywords in context.
    """

    kwic_raw = kwic(docs, search_tokens,
                    context_size=context_size,
                    match_type=match_type,
                    ignore_case=ignore_case,
                    glob_method=glob_method,
                    inverse=inverse,
                    with_metadata=False,
                    as_dict=True,
                    as_datatable=False,
                    non_empty=True,
                    glue=glue,
                    highlight_keyword=highlight_keyword)

    return _datatable_from_kwic_results(kwic_raw)


def glue_tokens(docs, patterns, glue='_', match_type='exact', ignore_case=False, glob_method='match', inverse=False,
                return_glued_tokens=False):
    """
    Match N *subsequent* tokens to the N patterns in `patterns` using match options like in
    :func:`~tmtoolkit.preprocess.filter_tokens`.
    Join the matched tokens by glue string `glue`. Replace these tokens in the documents.

    If there is metadata, the respective entries for the joint tokens are set to None.

    .. note:: This modifies the documents in `docs` in place.

    :param docs: list of tokenized documents
    :param patterns: a sequence of search patterns as excepted by :func:`~tmtoolkit.preprocess.filter_tokens`
    :param glue: string for joining the subsequent matches
    :param match_type: one of: 'exact', 'regex', 'glob'; if 'regex', `search_token` must be RE pattern; if `glob`,
                       `search_token` must be a "glob" pattern like "hello w*"
                       (see https://github.com/metagriffin/globre)
    :param ignore_case: if True, ignore case for matching
    :param glob_method: if `match_type` is 'glob', use this glob method; must be 'match' or 'search' (similar
                        behavior as Python's :func:`re.match` or :func:`re.search`)
    :param inverse: invert the matching results
    :param return_glued_tokens: if True, additionally return a set of tokens that were glued
    :return: in-place modified documents `docs` or, if `return_glued_tokens` is True, a 2-tuple with
             additional set of tokens that were glued
    """
    require_listlike(docs)

    glued_tokens = set()
    match_opts = {'match_type': match_type, 'ignore_case': ignore_case, 'glob_method': glob_method}

    # all documents must be compact before applying "token_glue_subsequent"
    docs = compact_documents(docs)

    for doc in docs:
        # no need to use _filtered_doc_tokens() here because tokens are compact already
        matches = token_match_subsequent(patterns, doc.user_data['tokens'], **match_opts)

        if inverse:
            matches = [~m for m in matches]

        _, glued = token_glue_subsequent(doc, matches, glue=glue, return_glued=True)
        glued_tokens.update(glued)

    if return_glued_tokens:
        return docs, glued_tokens
    else:
        return docs


def remove_chars(docs, chars):
    """
    Remove all characters listed in `chars` from all tokens.

    :param docs: list of tokenized documents
    :param chars: list of characters to remove
    :return: list of processed documents
    """
    require_listlike(docs)

    if not chars:
        raise ValueError('`chars` must be a non-empty sequence')

    del_chars = str.maketrans('', '', ''.join(chars))

    return [[t.translate(del_chars) for t in dtok] for dtok in docs]


def transform(docs, func, **kwargs):
    """
    Apply `func` to each token in each document of `docs` and return the result.

    :param docs: list of tokenized documents
    :param func: function to apply to each token; should accept a string as first arg. and optional `kwargs`
    :param kwargs: keyword arguments passed to `func`
    :return: list of processed documents
    """
    require_listlike(docs)

    if not callable(func):
        raise ValueError('`func` must be callable')

    if kwargs:
        func_wrapper = lambda t: func(t, **kwargs)
    else:
        func_wrapper = func

    return [list(map(func_wrapper, dtok)) for dtok in docs]


def to_lowercase(docs):
    """
    Apply lowercase transformation to each document.

    :param docs: list of tokenized documents
    :return: list of processed documents
    """
    return transform(docs, str.lower)


def pos_tag(docs, language=None, tagger_instance=None, doc_meta_key=None):
    """
    Apply Part-of-Speech (POS) tagging to list of documents `docs`. Either load a tagger based on supplied `language`
    or use the tagger instance `tagger` which must have a method ``tag()``. A tagger can be loaded via
    :func:`~tmtoolkit.preprocess.load_pos_tagger_for_language`.

    POS tagging so far only works for English and German. The English tagger uses the Penn Treebank tagset
    (https://ling.upenn.edu/courses/Fall_2003/ling001/penn_treebank_pos.html), the
    German tagger uses STTS (http://www.ims.uni-stuttgart.de/forschung/ressourcen/lexika/TagSets/stts-table.html).

    :param docs: list of tokenized documents
    :param language: the language for the POS tagger (currently only "english" and "german" are supported) if no
                    `tagger` is given
    :param tagger_instance: a tagger instance to use for tagging if no `language` is given
    :param doc_meta_key: if this is not None, it must be a string that specifies the key that is used for the
                         resulting dicts
    :return: if `doc_meta_key` is None, return a list of N lists, where N is the number of documents; each of these
             lists contains the POS tags for the respective tokens from `docs`, hence each POS list has the same length
             as the respective token list of the corresponding document; if `doc_meta_key` is not None, the result list
             contains dicts with the only key `doc_meta_key` that maps to the list of POS tags for the corresponding
             document
    """
    require_listlike(docs)    # TODO

    # if tagger_instance is None:
    #     tagger_instance, _ = load_pos_tagger_for_language(language or defaults.language)
    #
    # docs_meta = []
    # for dtok in docs:
    #     if len(dtok) > 0:
    #         tokens_and_tags = tagger_instance.tag(dtok)
    #         tags = list(list(zip(*tokens_and_tags))[1])
    #     else:
    #         tags = []
    #
    #     if doc_meta_key:
    #         docs_meta.append({doc_meta_key: tags})
    #     else:
    #         docs_meta.append(tags)
    #
    # return docs_meta


def lemmatize(docs, docs_meta, language=None, lemmatizer_fn=None):
    """
    Lemmatize documents according to `language` or use a custom lemmatizer function `lemmatizer_fn`.

    :param docs: list of tokenized documents
    :param docs_meta: list of meta data for each document in `docs` or list of POS tags per document; for option 1,
                      each element at index ``i`` is a dict containing the meta data for document ``i`` and each dict
                      must contain an element ``meta_pos`` with a list containing a POS tag for each token in the
                      respective document; for option 2, `docs_meta` is a list of POS tags for each document as coming
                      from :func:`~tmtoolkit.preprocess.pos_tag`
    :param language: the language for which the lemmatizer should be loaded
    :param lemmatizer_fn: alternatively, use this lemmatizer function; this function should accept a tuple consisting
                          of (token, POS tag)
    :return: list of processed documents
    """
    require_listlike(docs)    # TODO

    # if len(docs) != len(docs_meta):
    #     raise ValueError('`docs` and `docs_meta` must have the same length')
    #
    # if lemmatizer_fn is None:
    #     lemmatizer_fn = load_lemmatizer_for_language(language or defaults.language)
    #
    # new_tokens = []
    # for i, (dtok, dmeta) in enumerate(zip(docs, docs_meta)):
    #     if isinstance(dmeta, dict):
    #         if 'meta_pos' not in dmeta:
    #             raise ValueError('no POS meta data for document #%d' % i)
    #         dpos = dmeta['meta_pos']
    #     else:
    #         dpos = dmeta
    #
    #     if not isinstance(dpos, (list, tuple)) or len(dpos) != len(dtok):
    #         raise ValueError('provided POS tags for document #%d are invalid (no list/tuple and/or not of the same '
    #                          'length as the document')
    #
    #     new_tokens.append(list(map(lemmatizer_fn, zip(dtok, dpos))))
    #
    # return new_tokens


def expand_compounds(docs, split_chars=('-',), split_on_len=2, split_on_casechange=False):
    """
    Expand all compound tokens in documents `docs`, e.g. splitting token "US-Student" into two tokens "US" and
    "Student".

    :param docs: list of tokenized documents
    :param split_chars: characters to split on
    :param split_on_len: minimum length of a result token when considering splitting (e.g. when ``split_on_len=2``
                         "e-mail" would not be split into "e" and "mail")
    :param split_on_casechange: use case change to split tokens, e.g. "CamelCase" would become "Camel", "Case"
    :param flatten: if True, each document will be a flat list of tokens, otherwise each document will be a list
                    of lists, each containing one or more (split) tokens
    :return: list of processed documents
    """
    require_listlike(docs)

    exp_comp = partial(expand_compound_token, split_chars=split_chars, split_on_len=split_on_len,
                       split_on_casechange=split_on_casechange)

    exptoks = [list(map(exp_comp, _filtered_doc_tokens(doc))) for doc in docs]

    assert len(exptoks) == len(docs)
    new_docs = []
    for doc_exptok, doc in zip(exptoks, docs):
        words = []
        tokens = []
        spaces = []
        lemmata = []
        for exptok, t, oldtok in zip(doc_exptok, doc, _filtered_doc_tokens(doc)):
            n_exptok = len(exptok)
            spaces.extend([''] * (n_exptok-1) + [t.whitespace_])

            if n_exptok > 1:
                lemmata.extend(exptok)
                words.extend(exptok)
                tokens.extend(exptok)
            else:
                lemmata.append(t.lemma_)
                words.append(t.text)
                tokens.append(oldtok)

        new_doc = Doc(doc.vocab, words=words, spaces=spaces)
        new_doc._.label = doc._.label

        assert len(new_doc) == len(lemmata)
        _init_doc(new_doc, tokens)
        for t, lem in zip(new_doc, lemmata):
            t.lemma_ = lem

        new_docs.append(new_doc)

    return new_docs


def load_stopwords(language=None):
    """
    Load stopwords for language code `language`.

    :param language: two-letter ISO 639-1 language code
    :return: list of stopword strings or None if loading failed
    """

    language = language or defaults.language

    if not isinstance(language, str) or len(language) != 2:
        raise ValueError('`language` must be a two-letter ISO 639-1 language code')

    stopwords_pickle = os.path.join(DATAPATH, language, 'stopwords.pickle')
    try:
        with open(stopwords_pickle, 'rb') as f:
            return pickle.load(f)
    except (FileNotFoundError, IOError):
        return None


def clean_tokens(docs, remove_punct=True, remove_stopwords=True, remove_empty=True,
                 remove_shorter_than=None, remove_longer_than=None, remove_numbers=False, language=None):
    """
    Apply several token cleaning steps to documents `docs` and optionally documents metadata `docs_meta`, depending on
    the given parameters.

    :param docs: list of tokenized documents
    :param remove_punct: if True, remove all tokens that match the characters listed in ``string.punctuation`` from the
                         documents; if arg is a list, tuple or set, remove all tokens listed in this arg from the
                         documents; if False do not apply punctuation token removal
    :param remove_stopwords: if True, remove stop words for the given `language` as loaded via
                             `~tmtoolkit.preprocess.load_stopwords` ; if arg is a list, tuple or set, remove all tokens
                             listed in this arg from the documents; if False do not apply stop word token removal
    :param remove_empty: if True, remove empty strings ``""`` from documents
    :param remove_shorter_than: if given a positive number, remove tokens that are shorter than this number
    :param remove_longer_than: if given a positive number, remove tokens that are longer than this number
    :param remove_numbers: if True, remove all tokens that are deemed numeric by :func:`np.char.isnumeric`
    :param language: language for stop word removal
    :return: either list of processed documents or optional tuple with (processed documents, document meta data)
    """
    require_listlike(docs)

    # add empty token to list of tokens to remove
    tokens_to_remove = [''] if remove_empty else []

    # add punctuation characters to list of tokens to remove
    if isinstance(remove_punct, (tuple, list, set)):
        tokens_to_remove.extend(remove_punct)

    # add stopwords to list of tokens to remove
    if remove_stopwords is True:
        # default stopword list from NLTK
        tokens_to_remove.extend(load_stopwords(language))
    elif isinstance(remove_stopwords, (tuple, list, set)):
        tokens_to_remove.extend(remove_stopwords)

    # the "remove masks" list holds a binary array for each document where `True` signals a token to be removed
    remove_masks = [np.repeat(False, len(doc)) for doc in docs]

    # update remove mask for punctuation
    if remove_punct is True:
        remove_masks = [mask | doc.to_array('is_punct').astype(np.bool_)
                        for mask, doc in zip(remove_masks, docs)]

    # update remove mask for tokens shorter/longer than a certain number of characters
    if remove_shorter_than is not None or remove_longer_than is not None:
        token_lengths = [np.fromiter(map(len, doc), np.int, len(doc)) for doc in docs]

        if remove_shorter_than is not None:
            if remove_shorter_than < 0:
                raise ValueError('`remove_shorter_than` must be >= 0')
            remove_masks = [mask | (n < remove_shorter_than) for mask, n in zip(remove_masks, token_lengths)]

        if remove_longer_than is not None:
            if remove_longer_than < 0:
                raise ValueError('`remove_longer_than` must be >= 0')
            remove_masks = [mask | (n > remove_longer_than) for mask, n in zip(remove_masks, token_lengths)]

    # update remove mask for numeric tokens
    if remove_numbers:
        remove_masks = [mask | doc.to_array('like_num').astype(np.bool_)
                        for mask, doc in zip(remove_masks, docs)]

    # update remove mask for general list of tokens to be removed
    if tokens_to_remove:
        tokens_to_remove = set(tokens_to_remove)
        # this is actually much faster than using np.isin:
        remove_masks = [mask | np.array([t in tokens_to_remove for t in doc.user_data['tokens']], dtype=bool)
                        for mask, doc in zip(remove_masks, docs)]

    # apply the mask
    return _apply_matches_array(docs, remove_masks, invert=True)


def filter_tokens_by_mask(docs, mask, inverse=False):
    """
    Filter tokens in `docs` according to a binary mask specified by `mask`.

    :param docs: list of tokenized documents
    :param mask: a list containing a mask list for each document in `docs`; each mask list contains boolean values for
                 each token in that document, where `True` means keeping that token and `False` means removing it;
    :param inverse: inverse the mask for filtering, i.e. keep all tokens with a mask set to `False` and remove all those
                    with `True`
    :return: either list of processed documents or optional tuple with (processed documents, document meta data)
    """

    if len(mask) > 0 and not isinstance(mask[0], np.ndarray):
        mask = list(map(lambda x: np.array(x, dtype=np.bool), mask))

    return _apply_matches_array(docs, mask, invert=inverse)


def remove_tokens_by_mask(docs, mask):
    """
    Same as :func:`~tmtoolkit.preprocess.filter_tokens_by_mask` but with ``inverse=True``.

    .. seealso:: :func:`~tmtoolkit.preprocess.filter_tokens_by_mask`
    """
    return filter_tokens_by_mask(docs, mask, inverse=True)


def filter_tokens(docs, search_tokens, by_meta=None, match_type='exact', ignore_case=False,
                  glob_method='match', inverse=False):
    """
    Filter tokens in `docs` according to search pattern(s) `search_tokens` and several matching options. Only those
    tokens are retained that match the search criteria unless you set ``inverse=True``, which will *remove* all tokens
    that match the search criteria (which is the same as calling :func:`~tmtoolkit.preprocess.remove_tokens`).

    .. seealso:: :func:`~tmtoolkit.preprocess.remove_tokens`  and :func:`~tmtoolkit.preprocess.token_match`

    :param docs: list of tokenized documents
    :param search_tokens: single string or list of strings that specify the search pattern(s)
    :param by_meta: if not None, this should be a string of a meta data key in `docs_meta`; this meta data will then be
                    used for matching instead of the tokens in `docs`
    :param match_type: the type of matching that is performed: ``'exact'`` does exact string matching (optionally
                       ignoring character case if ``ignore_case=True`` is set); ``'regex'`` treats ``search_tokens``
                       as regular expressions to match the tokens against; ``'glob'`` uses "glob patterns" like
                       ``"politic*"`` which matches for example "politic", "politics" or ""politician" (see
                       `globre package <https://pypi.org/project/globre/>`_)
    :param ignore_case: ignore character case (applies to all three match types)
    :param glob_method: if `match_type` is 'glob', use this glob method. Must be 'match' or 'search' (similar
                        behavior as Python's :func:`re.match` or :func:`re.search`)
    :param inverse: inverse the match results for filtering (i.e. *remove* all tokens that match the search
                    criteria)
    :return: either list of processed documents or optional tuple with (processed documents, document meta data)
    """
    require_listlike(docs)

    matches = _token_pattern_matches(_match_against(docs, by_meta), search_tokens, match_type=match_type,
                                     ignore_case=ignore_case, glob_method=glob_method)

    return filter_tokens_by_mask(docs, matches, inverse=inverse)


def filter_tokens_with_kwic(docs, search_tokens, context_size=2, match_type='exact', ignore_case=False,
                            glob_method='match', inverse=False):
    """
    Filter tokens in `docs` according to Keywords-in-Context (KWIC) context window of size `context_size` around
    `search_tokens`. Works similar to :func:`~tmtoolkit.preprocess.kwic`, but returns result as list of tokenized
    documents, i.e. in the same structure as `docs` whereas :func:`~tmtoolkit.preprocess.kwic` returns result as
    list of *KWIC windows* into `docs`.

    .. seealso:: :func:`~tmtoolkit.preprocess.kwic`

    :param docs: list of tokenized documents
    :param search_tokens: single string or list of strings that specify the search pattern(s)
    :param context_size: either scalar int or tuple (left, right) -- number of surrounding words in keyword context.
                         if scalar, then it is a symmetric surrounding, otherwise can be asymmetric.
    :param match_type: the type of matching that is performed: ``'exact'`` does exact string matching (optionally
                       ignoring character case if ``ignore_case=True`` is set); ``'regex'`` treats ``search_tokens``
                       as regular expressions to match the tokens against; ``'glob'`` uses "glob patterns" like
                       ``"politic*"`` which matches for example "politic", "politics" or ""politician" (see
                       `globre package <https://pypi.org/project/globre/>`_)
    :param ignore_case: ignore character case (applies to all three match types)
    :param glob_method: if `match_type` is 'glob', use this glob method. Must be 'match' or 'search' (similar
                        behavior as Python's :func:`re.match` or :func:`re.search`)
    :param inverse: inverse the match results for filtering (i.e. *remove* all tokens that match the search
                    criteria)
    :return: either list of processed documents or optional tuple with (processed documents, document meta data)
    """
    require_listlike(docs)

    if isinstance(context_size, int):
        context_size = (context_size, context_size)
    else:
        require_listlike(context_size)

    matches = _build_kwic(docs, search_tokens,
                          context_size=context_size,
                          match_type=match_type,
                          ignore_case=ignore_case,
                          glob_method=glob_method,
                          inverse=inverse,
                          only_token_masks=True)

    return filter_tokens_by_mask(docs, matches)


def remove_tokens(docs, search_tokens, by_meta=None, match_type='exact', ignore_case=False,
                  glob_method='match'):
    """
    Same as :func:`~tmtoolkit.preprocess.filter_tokens` but with ``inverse=True``.

    .. seealso:: :func:`~tmtoolkit.preprocess.filter_tokens`  and :func:`~tmtoolkit.preprocess.token_match`
    """
    return filter_tokens(docs, search_tokens=search_tokens, by_meta=by_meta, match_type=match_type,
                         ignore_case=ignore_case, glob_method=glob_method, inverse=True)


def filter_documents(docs, search_tokens, by_meta=None, matches_threshold=1,
                     match_type='exact', ignore_case=False, glob_method='match', inverse_result=False,
                     inverse_matches=False):
    """
    This function is similar to :func:`~tmtoolkit.preprocess.filter_tokens` but applies at document level. For each
    document, the number of matches is counted. If it is at least `matches_threshold` the document is retained,
    otherwise removed. If `inverse_result` is True, then documents that meet the threshold are *removed*.

    .. seealso:: :func:`~tmtoolkit.preprocess.remove_documents`

    :param docs: list of tokenized documents
    :param search_tokens: single string or list of strings that specify the search pattern(s)
    :param by_meta: if not None, this should be a string of a meta data key in `docs_meta`; this meta data will then be
                    used for matching instead of the tokens in `docs`
    :param matches_threshold: the minimum number of matches required per document
    :param match_type: the type of matching that is performed: ``'exact'`` does exact string matching (optionally
                       ignoring character case if ``ignore_case=True`` is set); ``'regex'`` treats ``search_tokens``
                       as regular expressions to match the tokens against; ``'glob'`` uses "glob patterns" like
                       ``"politic*"`` which matches for example "politic", "politics" or ""politician" (see
                       `globre package <https://pypi.org/project/globre/>`_)
    :param ignore_case: ignore character case (applies to all three match types)
    :param glob_method: if `match_type` is 'glob', use this glob method. Must be 'match' or 'search' (similar
                        behavior as Python's :func:`re.match` or :func:`re.search`)
    :param inverse_result: inverse the threshold comparison result
    :param inverse_matches: inverse the match results for filtering
    :return: list of filtered documents
    """
    require_listlike(docs)

    matches = _token_pattern_matches(_match_against(docs, by_meta), search_tokens, match_type=match_type,
                                     ignore_case=ignore_case, glob_method=glob_method)

    if inverse_matches:
        matches = [~m for m in matches]

    new_docs = []
    for i, (doc, n_matches) in enumerate(zip(docs, map(np.sum, matches))):
        thresh_met = n_matches >= matches_threshold
        if thresh_met or (inverse_result and not thresh_met):
            new_docs.append(doc)

    return new_docs


def remove_documents(docs, search_tokens, by_meta=None, matches_threshold=1,
                     match_type='exact', ignore_case=False, glob_method='match', inverse_matches=False):
    """
    Same as :func:`~tmtoolkit.preprocess.filter_documents` but with ``inverse=True``.

    .. seealso:: :func:`~tmtoolkit.preprocess.filter_documents`
    """
    return filter_documents(docs, search_tokens, by_meta=by_meta,
                            matches_threshold=matches_threshold, match_type=match_type, ignore_case=ignore_case,
                            glob_method=glob_method, inverse_matches=inverse_matches, inverse_result=True)


def filter_documents_by_name(docs, name_patterns, match_type='exact', ignore_case=False,
                             glob_method='match', inverse=False):
    """
    Filter documents by their name (i.e. document label). Keep all documents whose name matches `name_pattern`
    according to additional matching options. If `inverse` is True, drop all those documents whose name matches,
    which is the same as calling :func:`~tmtoolkit.preprocess.remove_documents_by_name`.

    :param docs: list of tokenized documents
    :param name_patterns: either single search string or sequence of search strings
    :param match_type: the type of matching that is performed: ``'exact'`` does exact string matching (optionally
                       ignoring character case if ``ignore_case=True`` is set); ``'regex'`` treats ``search_tokens``
                       as regular expressions to match the tokens against; ``'glob'`` uses "glob patterns" like
                       ``"politic*"`` which matches for example "politic", "politics" or ""politician" (see
                       `globre package <https://pypi.org/project/globre/>`_)
    :param ignore_case: ignore character case (applies to all three match types)
    :param glob_method: if `match_type` is 'glob', use this glob method. Must be 'match' or 'search' (similar
                        behavior as Python's :func:`re.match` or :func:`re.search`)
    :param inverse: invert the matching results
    :return: list of filtered documents
    """
    require_listlike(docs)

    if isinstance(name_patterns, str):
        name_patterns = [name_patterns]

    matches = np.repeat(True, repeats=len(docs))
    doc_labels = _get_docs_attr(docs, 'label')

    for pat in name_patterns:
        pat_match = token_match(pat, doc_labels, match_type=match_type, ignore_case=ignore_case,
                                glob_method=glob_method)

        if inverse:
            pat_match = ~pat_match

        matches &= pat_match

    assert len(doc_labels) == len(matches)

    return [doc for doc, m in zip(docs, matches) if m]


def remove_documents_by_name(docs, name_patterns, match_type='exact', ignore_case=False,
                             glob_method='match'):
    """
    Same as :func:`~tmtoolkit.preprocess.filter_documents_by_name` but with ``inverse=True``.

    .. seealso:: :func:`~tmtoolkit.preprocess.filter_documents_by_name`
    """

    return filter_documents_by_name(docs, name_patterns, match_type=match_type,
                                    ignore_case=ignore_case, glob_method=glob_method)


def filter_for_pos(docs, required_pos, simplify_pos=True, tagset='ud', inverse=False):
    """
    Filter tokens for a specific POS tag (if `required_pos` is a string) or several POS tags (if `required_pos`
    is a list/tuple/set of strings).  The POS tag depends on the tagset used during tagging. See
    https://spacy.io/api/annotation#pos-tagging for a general overview on POS tags in SpaCy and refer to the
    documentation of your language model for specific tags.

    If `simplify_pos` is True, then the tags are matched to the following simplified forms:

    * ``'N'`` for nouns
    * ``'V'`` for verbs
    * ``'ADJ'`` for adjectives
    * ``'ADV'`` for adverbs
    * ``None`` for all other

    :param docs: list of tokenized documents
    :param required_pos: single string or list of strings with POS tag(s) used for filtering
    :param simplify_pos: before matching simplify POS tags in documents to forms shown above
    :param tagset: POS tagset used while tagging; necessary for simplifying POS tags when `simplify_pos` is True
    :param inverse: inverse the matching results, i.e. *remove* tokens that match the POS tag
    :return: filtered documents
    """
    require_listlike(docs)

    docs_pos = _get_docs_tokenattrs(docs, 'pos_', custom_attr=False)

    if not isinstance(required_pos, (tuple, list, set, str)) \
            and required_pos is not None:
        raise ValueError('`required_pos` must be a string, tuple, list, set or None')

    if required_pos is None or isinstance(required_pos, str):
        required_pos = [required_pos]

    if simplify_pos:
        simplify_fn = np.vectorize(lambda x: simplified_pos(x, tagset=tagset))
    else:
        simplify_fn = np.vectorize(lambda x: x)  # identity function

    matches = [np.isin(simplify_fn(dpos), required_pos)
               if len(dpos) > 0
               else np.array([], dtype=bool)
               for dpos in docs_pos]

    return _apply_matches_array(docs, matches, invert=inverse)


def remove_tokens_by_doc_frequency(docs, which, df_threshold, docs_meta=None, absolute=False, return_blacklist=False,
                                   return_mask=False):
    """
    Remove tokens according to their document frequency.

    :param docs: list of tokenized documents
    :param which: which threshold comparison to use: either ``'common'``, ``'>'``, ``'>='`` which means that tokens
                  with higher document freq. than (or equal to) `df_threshold` will be removed;
                  or ``'uncommon'``, ``'<'``, ``'<='`` which means that tokens with lower document freq. than
                  (or equal to) `df_threshold` will be removed
    :param df_threshold: document frequency threshold value
    :param docs_meta: list of meta data for each document in `docs`; each element at index ``i`` is a dict containing
                      the meta data for document ``i``; POS tags must exist for all documents in `docs_meta`
                      (``"meta_pos"`` key)
    :param absolute: if True, use absolute document frequency (i.e. number of times token X occurs at least once
                     in a document), otherwise use relative document frequency (normalized by number of documents)
    :param return_blacklist: if True return a list of tokens that should be removed instead of the filtered tokens
    :param return_mask: if True return a list of token masks where each occurrence of True signals a token to
                        be removed
    :return: when `return_blacklist` is True, return a list of tokens that should be removed; otherwise either return
             list of processed documents or optional tuple with (processed documents, document meta data)
    """
    require_listlike(docs)

    if docs_meta is not None:
        require_listlike(docs_meta)
        if len(docs) != len(docs_meta):
            raise ValueError('`docs` and `docs_meta` must have the same length')

    which_opts = {'common', '>', '>=', 'uncommon', '<', '<='}

    if which not in which_opts:
        raise ValueError('`which` must be one of: %s' % ', '.join(which_opts))

    n_docs = len(docs)

    if absolute:
        if not 0 <= df_threshold <= n_docs:
            raise ValueError('`df_threshold` must be in range [0, %d]' % n_docs)
    else:
        if not 0 <= df_threshold <= 1:
            raise ValueError('`df_threshold` must be in range [0, 1]')

    if which in ('common', '>='):
        comp = operator.ge
    elif which == '>':
        comp = operator.gt
    elif which == '<':
        comp = operator.lt
    else:
        comp = operator.le

    doc_freqs = doc_frequencies(docs, proportions=not absolute)
    mask = [[comp(doc_freqs[t], df_threshold) for t in dtok] for dtok in docs]

    if return_blacklist:
        blacklist = set(t for t, f in doc_freqs.items() if comp(f, df_threshold))
        if return_mask:
            return blacklist, mask

    if return_mask:
        return mask

    return remove_tokens_by_mask(docs, mask, docs_meta)


def remove_common_tokens(docs, docs_meta=None, df_threshold=0.95, absolute=False):
    """
    Shortcut for :func:`~tmtoolkit.preprocess.remove_tokens_by_doc_frequency` for removing tokens *above* a certain
    document frequency.

    :param docs: list of tokenized documents
    :param docs_meta: list of meta data for each document in `docs`; each element at index ``i`` is a dict containing
                      the meta data for document ``i``; POS tags must exist for all documents in `docs_meta`
                      (``"meta_pos"`` key)
    :param df_threshold: document frequency threshold value
    :param absolute: if True, use absolute document frequency (i.e. number of times token X occurs at least once
                 in a document), otherwise use relative document frequency (normalized by number of documents)
    :return: either list of processed documents or optional tuple with (processed documents, document meta data)
    """
    return remove_tokens_by_doc_frequency(docs, 'common', df_threshold=df_threshold, docs_meta=docs_meta,
                                          absolute=absolute)


def remove_uncommon_tokens(docs, docs_meta=None, df_threshold=0.05, absolute=False):
    """
    Shortcut for :func:`~tmtoolkit.preprocess.remove_tokens_by_doc_frequency` for removing tokens *below* a certain
    document frequency.

    :param docs: list of tokenized documents
    :param docs_meta: list of meta data for each document in `docs`; each element at index ``i`` is a dict containing
                      the meta data for document ``i``; POS tags must exist for all documents in `docs_meta`
                      (``"meta_pos"`` key)
    :param df_threshold: document frequency threshold value
    :param absolute: if True, use absolute document frequency (i.e. number of times token X occurs at least once
                 in a document), otherwise use relative document frequency (normalized by number of documents)
    :return: either list of processed documents or optional tuple with (processed documents, document meta data)
    """
    return remove_tokens_by_doc_frequency(docs, 'uncommon', df_threshold=df_threshold, docs_meta=docs_meta,
                                          absolute=absolute)


def compact_documents(docs):
    """
    Compact documents `docs` by recreating new documents using the previously applied filters.

    :param docs: list of tokenized documents
    :return: list with compact documents
    """
    return _apply_matches_array(docs, compact=True)


def tokens2ids(docs, return_counts=False):
    """
    Convert a character token array `tok` to a numeric token ID array.
    Return the vocabulary array (char array where indices correspond to token IDs) and token ID array.
    Optionally return the counts of each token in the token ID array when `return_counts` is True.

    .. seealso:: :func:`~tmtoolkit.preprocess.ids2tokens` which reverses this operation.

    :param docs: list of tokenized documents
    :param return_counts: if True, also return array with counts of each unique token in `tok`
    :return: tuple with (vocabulary array, documents as arrays with token IDs) and optional counts
    """
    if not docs:
        if return_counts:
            return empty_chararray(), [], np.array([], dtype=int)
        else:
            return empty_chararray(), []

    if not isinstance(docs[0], np.ndarray):
        docs = list(map(np.array, docs))

    res = np.unique(np.concatenate(docs), return_inverse=True, return_counts=return_counts)

    if return_counts:
        vocab, all_tokids, vocab_counts = res
    else:
        vocab, all_tokids = res

    vocab = vocab.astype(np.str)
    doc_tokids = np.split(all_tokids, np.cumsum(list(map(len, docs))))[:-1]

    if return_counts:
        return vocab, doc_tokids, vocab_counts
    else:
        return vocab, doc_tokids


def ids2tokens(vocab, tokids):
    """
    Convert list of numeric token ID arrays `tokids` to a character token array with the help of the vocabulary
    array `vocab`.
    Returns result as list of string token arrays.

    .. seealso:: :func:`~tmtoolkit.preprocess.tokens2ids` which reverses this operation.

    :param vocab: vocabulary array as from :func:`~tmtoolkit.preprocess.tokens2ids`
    :param tokids: list of numeric token ID arrays as from :func:`~tmtoolkit.preprocess.tokens2ids`
    :return: list of string token arrays
    """
    return [vocab[ids] for ids in tokids]


#%% functions that operate on single token documents (lists or arrays of string tokens)


def token_match(pattern, tokens, match_type='exact', ignore_case=False, glob_method='match'):
    """
    Return a boolean NumPy array signaling matches between `pattern` and `tokens`. `pattern` is a string that will be
    compared with each element in sequence `tokens` either as exact string equality (`match_type` is ``'exact'``) or
    regular expression (`match_type` is ``'regex'``) or glob pattern (`match_type` is ``'glob'``).

    :param pattern: either a string or a compiled RE pattern used for matching against `tokens`
    :param tokens: list or NumPy array of string tokens
    :param match_type: one of: 'exact', 'regex', 'glob'; if 'regex', `search_token` must be RE pattern; if `glob`,
                       `search_token` must be a "glob" pattern like "hello w*"
                       (see https://github.com/metagriffin/globre)
    :param ignore_case: if True, ignore case for matching
    :param glob_method: if `match_type` is 'glob', use this glob method. Must be 'match' or 'search' (similar
                        behavior as Python's `re.match` or `re.search`)
    :return: 1D boolean NumPy array of length ``len(tokens)`` where elements signal matches between `pattern` and the
             respective token from `tokens`
    """
    if match_type not in {'exact', 'regex', 'glob'}:
        raise ValueError("`match_type` must be one of `'exact', 'regex', 'glob'`")

    if len(tokens) == 0:
        return np.array([], dtype=bool)

    if not isinstance(tokens, np.ndarray):
        tokens = np.array(tokens)

    ignore_case_flag = dict(flags=re.IGNORECASE) if ignore_case else {}

    if match_type == 'exact':
        return np.char.lower(tokens) == pattern.lower() if ignore_case else tokens == pattern
    elif match_type == 'regex':
        if isinstance(pattern, str):
            pattern = re.compile(pattern, **ignore_case_flag)
        vecmatch = np.vectorize(lambda x: bool(pattern.search(x)))
        return vecmatch(tokens)
    else:
        if glob_method not in {'search', 'match'}:
            raise ValueError("`glob_method` must be one of `'search', 'match'`")

        if isinstance(pattern, str):
            pattern = globre.compile(pattern, **ignore_case_flag)

        if glob_method == 'search':
            vecmatch = np.vectorize(lambda x: bool(pattern.search(x)))
        else:
            vecmatch = np.vectorize(lambda x: bool(pattern.match(x)))

        return vecmatch(tokens) if len(tokens) > 0 else np.array([], dtype=bool)


def token_match_subsequent(patterns, tokens, **kwargs):
    """
    Using N patterns in `patterns`, return each tuple of N matching subsequent tokens from `tokens`. Excepts the same
    token matching options via `kwargs` as :func:`~tmtoolkit.preprocess.token_match`. The results are returned as list
    of NumPy arrays with indices into `tokens`.

    Example::

        # indices:   0        1        2         3        4       5       6
        tokens = ['hello', 'world', 'means', 'saying', 'hello', 'world', '.']

        token_match_subsequent(['hello', 'world'], tokens)
        # [array([0, 1]), array([4, 5])]

        token_match_subsequent(['world', 'hello'], tokens)
        # []

        token_match_subsequent(['world', '*'], tokens, match_type='glob')
        # [array([1, 2]), array([5, 6])]

    .. seealso:: :func:`~tmtoolkit.preprocess.token_match`

    :param patterns: A sequence of search patterns as excepted by :func:`~tmtoolkit.preprocess.token_match`
    :param tokens: A sequence of tokens to be used for matching.
    :param kwargs: Token matching options as passed to :func:`~tmtoolkit.preprocess.token_match`
    :return: List of NumPy arrays with subsequent indices into `tokens`
    """
    require_listlike(patterns)

    n_pat = len(patterns)

    if n_pat < 2:
        raise ValueError('`patterns` must contain at least two strings')

    n_tok = len(tokens)

    if n_tok == 0:
        return []

    if not isinstance(tokens, np.ndarray):
        tokens = np.array(tokens)

    # iterate through the patterns
    for i_pat, pat in enumerate(patterns):
        if i_pat == 0:   # initial matching on full token array
            next_indices = np.arange(n_tok)
        else:  # subsequent matching uses previous match indices + 1 to match on tokens right after the previous matches
            next_indices = match_indices + 1
            next_indices = next_indices[next_indices < n_tok]   # restrict maximum index

        # do the matching with the current subset of "tokens"
        pat_match = token_match(pat, tokens[next_indices], **kwargs)

        # pat_match is boolean array. use it to select the token indices where we had a match
        # this is used in the next iteration again to select the tokens right after these matches
        match_indices = next_indices[pat_match]

        if len(match_indices) == 0:   # anytime when no successful match appeared, we can return the empty result
            return []                 # because *all* subsequent patterns must match corresponding subsequent tokens

    # at this point, match_indices contains indices i that point to the *last* matched token of the `n_pat` subsequently
    # matched tokens

    assert np.min(match_indices) - n_pat + 1 >= 0
    assert np.max(match_indices) < n_tok

    # so we can use this to reconstruct the whole "trace" subsequently matched indices as final result
    return list(map(lambda i: np.arange(i - n_pat + 1, i + 1), match_indices))


def token_glue_subsequent(doc, matches, glue='_', return_glued=False):
    """
    Select subsequent tokens in `doc` as defined by list of indices `matches` (e.g. output of
    :func:`~tmtoolkit.preprocess.token_match_subsequent`) and join those by string `glue`. Return `doc` again.

    .. note:: This function modifies `doc` in place. All token attributes will be reset to default values besides
              ``"lemma_"`` which are set to the joint token string.

    .. warning:: Only works correctly when matches contains indices of *subsequent* tokens.

    Example::

        # doc is a SpaCy document with tokens ['a', 'b', 'c', 'd', 'd', 'a', 'b', 'c']
        token_glue_subsequent(doc, [np.array([1, 2]), np.array([6, 7])])
        # doc now contains tokens ['a', 'b_c', 'd', 'd', 'a', 'b_c']

    .. seealso:: :func:`~tmtoolkit.preprocess.token_match_subsequent`

    :param doc: a SpaCy document which must be compact (i.e. no filter mask set)
    :param matches: list of NumPy arrays with *subsequent* indices into `tokens` (e.g. output of
                    :func:`~tmtoolkit.preprocess.token_match_subsequent`)
    :param glue: string for joining the subsequent matches or None if no joint tokens but an empty string should be set
                 as token value and lemma
    :param return_glued: if yes, return also a list of joint tokens
    :return: either two-tuple or input `doc`; if `return_glued` is True, return a two-tuple with 1) `doc` and 2) a list
             of joint tokens; if `return_glued` is True only return 1)
    """
    require_listlike(matches)

    if return_glued and glue is None:
        raise ValueError('if `glue` is None, `return_glued` must be False')

    if not doc.user_data['mask'].all():
        raise ValueError('`doc` must be compact, i.e. no filter mask should be set (all elements in '
                         '`doc.user_data["mask"]` must be True)')

    n_tok = len(doc)

    if n_tok == 0:
        if return_glued:
            return doc, []
        else:
            return doc

    # map span start index to end index
    glued = []

    # within this context, `doc` doesn't change (i.e. we can use the same indices into `doc` throughout the for loop
    # even when we merge tokens
    del_tokens_indices = []
    with doc.retokenize() as retok:
        # we will need to update doc.user_data['tokens'], which is a NumPy character array;
        # a NumPy char array has a maximum element size and we will need to update that to the
        # maximum string length in `chararray_updates` by using `widen_chararray()` below
        chararray_updates = {}
        for m in matches:
            assert len(m) >= 2
            begin, end = m[0], m[-1]
            span = doc[begin:end+1]
            merged = '' if glue is None else glue.join(doc.user_data['tokens'][begin:end+1])
            assert begin not in chararray_updates.keys()
            chararray_updates[begin] = merged
            del_tokens_indices.extend(list(range(begin+1, end+1)))
            attrs = {
                'LEMMA': merged,
                'WHITESPACE': doc[end].whitespace_
            }
            retok.merge(span, attrs=attrs)

            if return_glued:
                glued.append(merged)

        if chararray_updates:
            new_maxsize = max(map(len, chararray_updates.values()))
            doc.user_data['tokens'] = widen_chararray(doc.user_data['tokens'], new_maxsize)
            for begin, merged in chararray_updates.items():
                doc.user_data['tokens'][begin] = merged

    doc.user_data['tokens'] = np.delete(doc.user_data['tokens'], del_tokens_indices)
    doc.user_data['mask'] = np.delete(doc.user_data['mask'], del_tokens_indices)

    if return_glued:
        return doc, glued
    else:
        return doc


def make_index_window_around_matches(matches, left, right, flatten=False, remove_overlaps=True):
    """
    Take a boolean 1D vector `matches` of length N and generate an array of indices, where each occurrence of a True
    value in the boolean vector at index i generates a sequence of the form:

    .. code-block:: text

        [i-left, i-left+1, ..., i, ..., i+right-1, i+right, i+right+1]

    If `flatten` is True, then a flattened NumPy 1D array is returned. Otherwise, a list of NumPy arrays is returned,
    where each array contains the window indices.

    `remove_overlaps` is only applied when `flatten` is True.

    Example with ``left=1 and right=1, flatten=False``:

    .. code-block:: text

        input:
        #   0      1      2      3     4      5      6      7     8
        [True, True, False, False, True, False, False, False, True]
        output (matches *highlighted*):
        [[0, *1*], [0, *1*, 2], [3, *4*, 5], [7, *8*]]

    Example with ``left=1 and right=1, flatten=True, remove_overlaps=True``:

    .. code-block:: text

        input:
        #   0      1      2      3     4      5      6      7     8
        [True, True, False, False, True, False, False, False, True]
        output (matches *highlighted*, other values belong to the respective "windows"):
        [*0*, *1*, 2, 3, *4*, 5, 7, *8*]
    """
    if not isinstance(matches, np.ndarray) or matches.dtype != bool:
        raise ValueError('`matches` must be a boolean NumPy array')
    if not isinstance(left, int) or left < 0:
        raise ValueError('`left` must be an integer >= 0')
    if not isinstance(right, int) or right < 0:
        raise ValueError('`right` must be an integer >= 0')

    ind = np.where(matches)[0]
    nested_ind = list(map(lambda x: np.arange(x - left, x + right + 1), ind))

    if flatten:
        if not nested_ind:
            return np.array([], dtype=np.int)

        window_ind = np.concatenate(nested_ind)
        window_ind = window_ind[(window_ind >= 0) & (window_ind < len(matches))]

        if remove_overlaps:
            return np.sort(np.unique(window_ind))
        else:
            return window_ind
    else:
        return [w[(w >= 0) & (w < len(matches))] for w in nested_ind]


#%% functions that operate on single tokens / strings


def expand_compound_token(t, split_chars=('-',), split_on_len=2, split_on_casechange=False):
    """
    Expand a token `t` if it is a compound word, e.g. splitting token "US-Student" into two tokens "US" and
    "Student".

    .. seealso:: :func:`~tmtoolkit.preprocess.expand_compounds` which operates on token documents

    :param t: string token
    :param split_chars: characters to split on
    :param split_on_len: minimum length of a result token when considering splitting (e.g. when ``split_on_len=2``
                         "e-mail" would not be split into "e" and "mail")
    :param split_on_casechange: use case change to split tokens, e.g. "CamelCase" would become "Camel", "Case"
    :return: list with split sub-tokens or single original token, i.e. ``[t]``
    """
    if not isinstance(t, str):
        raise ValueError('`t` must be a string')

    if isinstance(split_chars, str):
        split_chars = (split_chars,)

    require_listlike_or_set(split_chars)

    if split_on_len is not None and split_on_len < 1:
        raise ValueError('`split_on_len` must be greater or equal 1')

    if split_on_casechange and not split_chars:
        t_parts = str_shapesplit(t, min_part_length=split_on_len)
    else:
        split_chars = set(split_chars)
        t_parts = str_multisplit(t, split_chars)

        if split_on_casechange:
            t_parts = flatten_list([str_shapesplit(p, min_part_length=split_on_len) for p in t_parts])

    n_parts = len(t_parts)
    assert n_parts > 0

    if n_parts == 1:
        return t_parts
    else:
        parts = []
        add = False  # signals if current part should be appended to previous part

        for p in t_parts:
            if not p: continue  # skip empty part
            if add and parts:   # append current part p to previous part
                parts[-1] += p
            else:               # add p as separate token
                parts.append(p)

            if split_on_len:
                # if p consists of less than `split_on_len` characters -> append the next p to it
                add = len(p) < split_on_len

            if split_on_casechange:
                # alt. strategy: if p is all uppercase ("US", "E", etc.) -> append the next p to it
                add = add and p.isupper() if split_on_len else p.isupper()

        if add and len(parts) >= 2:
            parts = parts[:-2] + [parts[-2] + parts[-1]]

        return parts or [t]


def str_multisplit(s, split_chars):
    """
    Split string `s` by all characters in `split_chars`.

    :param s: a string to split
    :param split_chars: sequence or set of characters to use for splitting
    :return: list of split string parts
    """
    if not isinstance(s, (str, bytes)):
        raise ValueError('`s` must be of type `str` or `bytes`')

    require_listlike_or_set(split_chars)

    parts = [s]
    for c in split_chars:
        parts_ = []
        for p in parts:
            parts_.extend(p.split(c))
        parts = parts_

    return parts


def str_shape(s, lower=0, upper=1, as_str=False):
    """
    Generate a sequence that reflects the "shape" of string `s`.

    :param s: input string
    :param lower: shape element marking a lower case letter
    :param upper: shape element marking an upper case letter
    :param as_str: join the sequence to a string
    :return: shape list or string if `as_str` is True
    """
    shape = [lower if c.islower() else upper for c in s]

    if as_str:
        if not isinstance(lower, str) or not isinstance(upper, str):
            shape = map(str, shape)

        return ''.join(shape)

    return shape


def str_shapesplit(s, shape=None, min_part_length=2):
    """
    Split string `s` according to its "shape" which is either given by `shape` (see
    :func:`~tmtoolkit.preprocess.str_shape`).

    :param s: string to split
    :param shape: list where 0 denotes a lower case character and 1 an upper case character; if `shape` is None,
                  it is computed via :func:`~tmtoolkit.preprocess.str_shape()`
    :param min_part_length: minimum length of a chunk (as long as ``len(s) >= min_part_length``)
    :return: list of substrings of `s`; returns ``['']`` if `s` is empty string
    """

    if not isinstance(s, str):
        raise ValueError('`s` must be string')

    if min_part_length is None:
        min_part_length = 2

    if min_part_length < 1:
        raise ValueError('`min_part_length` must be greater or equal 1')

    if not s:
        return ['']

    if shape is None:
        shape = str_shape(s)
    elif len(shape) != len(s):
        raise ValueError('`shape` must have same length as `s`')

    shapechange = np.abs(np.diff(shape, prepend=[shape[0]])).tolist()
    assert len(s) == len(shape) == len(shapechange)

    parts = []
    n = 0
    while shapechange and n < len(s):
        if n == 0:
            begin = 0
        else:
            begin = shapechange.index(1, n)

        try:
            offset = n + 1 if n == 0 and shape[0] == 0 else n + min_part_length
            end = shapechange.index(1, offset)
            #end = shapechange.index(1, n+min_part_length)
            n += end - begin
        except ValueError:
            end = None
            n = len(s)

        chunk = s[begin:end]

        if (parts and len(parts[-1]) >= min_part_length and len(chunk) >= min_part_length) or not parts:
            parts.append(chunk)
        else:
            parts[-1] += chunk

    return parts



#%% Part-of-Speech tag handling


def pos_tag_convert_penn_to_wn(tag):
    """
    Convert POS tag from Penn tagset to WordNet tagset.

    :param tag: a tag from Penn tagset
    :return: a tag from WordNet tagset or None if no corresponding tag could be found
    """
    from nltk.corpus import wordnet as wn

    if tag in ['JJ', 'JJR', 'JJS']:
        return wn.ADJ
    elif tag in ['RB', 'RBR', 'RBS']:
        return wn.ADV
    elif tag in ['NN', 'NNS', 'NNP', 'NNPS']:
        return wn.NOUN
    elif tag in ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']:
        return wn.VERB
    return None


def simplified_pos(pos, tagset='ud', default=''):
    """
    Return a simplified POS tag for a full POS tag `pos` belonging to a tagset `tagset`.

    Does the following conversion by default:

    - all N... (noun) tags to 'N'
    - all V... (verb) tags to 'V'
    - all ADJ... (adjective) tags to 'ADJ'
    - all ADV... (adverb) tags to 'ADV'
    - all other to `default`

    Does the following conversion by with ``tagset=='penn'``:

    - all N... (noun) tags to 'N'
    - all V... (verb) tags to 'V'
    - all JJ... (adjective) tags to 'ADJ'
    - all RB... (adverb) tags to 'ADV'
    - all other to `default`

    Does the following conversion by with ``tagset=='ud'``:

    - all N... (noun) tags to 'N'
    - all V... (verb) tags to 'V'
    - all JJ... (adjective) tags to 'ADJ'
    - all RB... (adverb) tags to 'ADV'
    - all other to `default`

    :param pos: a POS tag
    :param tagset: tagset used for `pos`; can be ``'wn'`` (WordNet), ``'penn'`` (Penn tagset)
                   or ``'ud'`` (universal dependencies – default)
    :param default: default return value when tag could not be simplified
    :return: simplified tag
    """

    if tagset == 'ud':
        if pos in ('NOUN', 'PROPN'):
            return 'N'
        elif pos == 'VERB':
            return 'V'
        elif pos in ('ADJ', 'ADV'):
            return pos
        else:
            return default
    elif tagset == 'penn':
        if pos.startswith('N') or pos.startswith('V'):
            return pos[0]
        elif pos.startswith('JJ'):
            return 'ADJ'
        elif pos.startswith('RB'):
            return 'ADV'
        else:
            return default
    elif tagset == 'wn':   # default: WordNet
        if pos.startswith('N') or pos.startswith('V'):
            return pos[0]
        elif pos.startswith('ADJ') or pos.startswith('ADV'):
            return pos[:3]
        else:
            return default
    else:
        raise ValueError('unknown tagset "%s"' % tagset)


#%% helper functions and classes


def _current_nlp(nlp_instance):
    _nlp = nlp_instance or nlp
    if not _nlp:
        raise ValueError('neither global nlp instance is set, nor `nlp_instance` argument is given; did you call '
                         '`init_for_language()` before?')
    return _nlp


def _build_kwic(docs, search_tokens, context_size, match_type, ignore_case, glob_method, inverse,
                highlight_keyword=None, with_metadata=False, with_window_indices=False,
                only_token_masks=False):
    """
    Helper function to build keywords-in-context (KWIC) results from documents `docs`.

    :param docs: list of tokenized documents
    :param search_tokens: search pattern(s)
    :param context_size: either scalar int or tuple (left, right) -- number of surrounding words in keyword context.
                         if scalar, then it is a symmetric surrounding, otherwise can be asymmetric
    :param match_type: One of: 'exact', 'regex', 'glob'. If 'regex', `search_token` must be RE pattern. If `glob`,
                       `search_token` must be a "glob" pattern like "hello w*"
                       (see https://github.com/metagriffin/globre).
    :param ignore_case: If True, ignore case for matching.
    :param glob_method: If `match_type` is 'glob', use this glob method. Must be 'match' or 'search' (similar
                        behavior as Python's :func:`re.match` or :func:`re.search`).
    :param inverse: Invert the matching results.
    :param highlight_keyword: If not None, this must be a string which is used to indicate the start and end of the
                              matched keyword.
    :param with_metadata: add document metadata to KWIC results
    :param with_window_indices: add window indices to KWIC results
    :param only_token_masks: return only flattened token masks for filtering
    :return: list with KWIC results per document
    """

    # find matches for search criteria -> list of NumPy boolean mask arrays
    matches = _token_pattern_matches(_match_against(docs), search_tokens, match_type=match_type,
                                     ignore_case=ignore_case, glob_method=glob_method)

    if not only_token_masks and inverse:
        matches = [~m for m in matches]

    left, right = context_size

    kwic_list = []
    for mask, doc in zip(matches, docs):
        ind = np.where(mask)[0]
        ind_windows = make_index_window_around_matches(mask, left, right,
                                                       flatten=only_token_masks, remove_overlaps=True)

        if only_token_masks:
            assert ind_windows.ndim == 1
            assert len(ind) <= len(ind_windows)

            # from indices back to binary mask; this only works with remove_overlaps=True
            win_mask = np.repeat(False, len(doc))
            win_mask[ind_windows] = True

            if inverse:
                win_mask = ~win_mask

            kwic_list.append(win_mask)
        else:
            doc_arr = _filtered_doc_tokens(doc)

            assert len(ind) == len(ind_windows)

            windows_in_doc = []
            for match_ind, win in zip(ind, ind_windows):  # win is an array of indices into dtok_arr
                tok_win = doc_arr[win].tolist()

                if highlight_keyword is not None:
                    highlight_mask = win == match_ind
                    assert np.sum(highlight_mask) == 1
                    highlight_ind = np.where(highlight_mask)[0][0]
                    tok_win[highlight_ind] = highlight_keyword + tok_win[highlight_ind] + highlight_keyword

                win_res = {'token': tok_win}

                if with_window_indices:
                    win_res['index'] = win

                if with_metadata:
                    attr_keys = _get_docs_tokenattrs_keys(docs)
                    attr_vals = {}
                    attr_keys_base = []
                    attr_keys_ext = []

                    for attr in attr_keys:
                        baseattr = attr.endswith('_')

                        if baseattr:
                            attrlabel = attr[:-1]   # remove trailing underscore
                            attr_keys_base.append(attrlabel)
                        else:
                            attrlabel = attr
                            attr_keys_ext.append(attrlabel)

                        if attr == 'whitespace_':
                            attrdata = [bool(t.whitespace_) for t in doc]
                        else:
                            attrdata = [getattr(t if baseattr else t._, attr) for t in doc]

                        attr_vals[attrlabel] = np.array(attrdata)[win].tolist()

                    for attrlabel in list(sorted(attr_keys_base)) + list(sorted(attr_keys_ext)):
                        win_res[attrlabel] = attr_vals[attrlabel]

                windows_in_doc.append(win_res)

            kwic_list.append(windows_in_doc)

    assert len(kwic_list) == len(docs)

    return kwic_list


def _finalize_kwic_results(kwic_results, non_empty, glue, as_datatable, with_metadata):
    """
    Helper function to finalize raw KWIC results coming from `_build_kwic()`: Filter results, "glue" (join) tokens,
    transform to datatable, return or dismiss metadata.
    """
    kwic_results_ind = None

    if non_empty:
        if isinstance(kwic_results, dict):
            kwic_results = {dl: windows for dl, windows in kwic_results.items() if len(windows) > 0}
        else:
            assert isinstance(kwic_results, (list, tuple))
            kwic_results_w_indices = [(i, windows) for i, windows in enumerate(kwic_results) if len(windows) > 0]
            if kwic_results_w_indices:
                kwic_results_ind, kwic_results = zip(*kwic_results_w_indices)
            else:
                kwic_results_ind = []
                kwic_results = []

    if glue is not None:
        if isinstance(kwic_results, dict):
            return {dl: [glue.join(win['token']) for win in windows] for dl, windows in kwic_results.items()}
        else:
            assert isinstance(kwic_results, (list, tuple))
            return [[glue.join(win['token']) for win in windows] for windows in kwic_results]
    elif as_datatable:
        dfs = []
        if not kwic_results_ind:
            kwic_results_ind = range(len(kwic_results))

        for i_doc, dl_or_win in zip(kwic_results_ind, kwic_results):
            if isinstance(kwic_results, dict):
                dl = dl_or_win
                windows = kwic_results[dl]
            else:
                dl = i_doc
                windows = dl_or_win

            for i_win, win in enumerate(windows):
                if isinstance(win, list):
                    win = {'token': win}

                n_tok = len(win['token'])
                df_windata = [np.repeat(dl, n_tok),
                              np.repeat(i_win, n_tok),
                              win['index'],
                              win['token']]

                if with_metadata:
                    meta_cols = [col for col in win.keys() if col not in {'token', 'index'}]
                    df_windata.extend([win[col] for col in meta_cols])
                else:
                    meta_cols = []

                df_cols = ['doc', 'context', 'position', 'token'] + meta_cols
                dfs.append(pd_dt_frame(OrderedDict(zip(df_cols, df_windata))))

        if dfs:
            kwic_df = pd_dt_concat(dfs)
            return pd_dt_sort(kwic_df, ('doc', 'context', 'position'))
        else:
            return pd_dt_frame(OrderedDict(zip(['doc', 'context', 'position', 'token'], [[] for _ in range(4)])))
    elif not with_metadata:
        if isinstance(kwic_results, dict):
            return {dl: [win['token'] for win in windows]
                    for dl, windows in kwic_results.items()}
        else:
            return [[win['token'] for win in windows] for windows in kwic_results]
    else:
        return kwic_results


def _datatable_from_kwic_results(kwic_results):
    """
    Helper function to transform raw KWIC results coming from `_build_kwic()` to a datatable for `kwic_table()`.
    """
    dfs = []

    for i_doc, dl_or_win in enumerate(kwic_results):
        if isinstance(kwic_results, dict):
            dl = dl_or_win
            windows = kwic_results[dl]
        else:
            dl = i_doc
            windows = dl_or_win

        dfs.append(pd_dt_frame(OrderedDict(zip(['doc', 'context', 'kwic'],
                                               [np.repeat(dl, len(windows)), np.arange(len(windows)), windows]))))
    if dfs:
        kwic_df = pd_dt_concat(dfs)
        return pd_dt_sort(kwic_df, ('doc', 'context'))
    else:
        return pd_dt_frame(OrderedDict(zip(['doc', 'context', 'kwic'], [[] for _ in range(3)])))


def _ngrams_from_tokens(tokens, n, join=True, join_str=' '):
    """
    Helper function to produce ngrams of length `n` from a list of string tokens `tokens`.

    :param tokens: list of string tokens
    :param n: size of ngrams
    :param join: if True, join each ngram by `join_str`, i.e. return list of ngram strings; otherwise return list of
                 ngram lists
    :param join_str: if `join` is True, use this string to join the parts of the ngrams
    :return: return list of ngram strings if `join` is True, otherwise list of ngram lists
    """
    if n < 2:
        raise ValueError('`n` must be at least 2')

    if len(tokens) == 0:
        return []

    if len(tokens) < n:
        # raise ValueError('`len(tokens)` should not be smaller than `n`')
        ngrams = [tokens]
    else:
        ngrams = [[tokens[i+j] for j in range(n)]
                  for i in range(len(tokens)-n+1)]
    if join:
        return list(map(lambda x: join_str.join(x), ngrams))
    else:
        return ngrams


def _match_against(docs, by_meta=None):
    """Return the list of values to match against in filtering functions."""
    if by_meta:
        return [_filtered_doc_arr([getattr(t._, by_meta) for t in doc], doc) for doc in docs]
    else:
        return [_filtered_doc_tokens(doc) for doc in docs]


def _token_pattern_matches(tokens, search_tokens, match_type, ignore_case, glob_method):
    """
    Helper function to apply `token_match` with multiple patterns in `search_tokens` to `docs`.
    The matching results for each pattern in `search_tokens` are combined via logical OR.
    Returns a list of length `docs` containing boolean arrays that signal the pattern matches for each token in each
    document.
    """
    if not isinstance(search_tokens, (list, tuple, set)):
        search_tokens = [search_tokens]

    matches = [np.repeat(False, repeats=len(dtok)) for dtok in tokens]

    for dtok, dmatches in zip(tokens, matches):
        for pat in search_tokens:
            pat_match = token_match(pat, dtok, match_type=match_type, ignore_case=ignore_case, glob_method=glob_method)

            dmatches |= pat_match

    return matches


def _apply_matches_array(docs, matches=None, invert=False, compact=False):
    """
    Helper function to apply a list of boolean arrays `matches` that signal token to pattern matches to a list of
    tokenized documents `docs`. If `compact` is False, simply set the new filter mask to previously unfiltered elements,
    which changes document masks in-place. If `compact` is True, create new Doc objects from filtered data *if there
    are any filtered tokens*, otherwise return the same unchanged Doc object.
    """

    if matches is None:
        matches = [doc.user_data['mask'] for doc in docs]

    if invert:
        matches = [~m for m in matches]

    assert len(matches) == len(docs)

    if compact:   # create new Doc objects from filtered data
        more_attrs = _get_docs_tokenattrs_keys(docs, default_attrs=['lemma_'])
        new_docs = []

        for mask, doc in zip(matches, docs):
            assert len(mask) == len(doc) == len(doc.user_data['tokens'])

            if mask.all():
                new_doc = doc
            else:
                filtered = [(t, tt) for m, t, tt in zip(mask, doc, doc.user_data['tokens']) if m]

                if filtered:
                    new_doc_text, new_doc_spaces = list(zip(*[(t.text, t.whitespace_) for t, _ in filtered]))
                else:  # empty doc.
                    new_doc_text = []
                    new_doc_spaces = []

                new_doc = Doc(doc.vocab, words=new_doc_text, spaces=new_doc_spaces)
                new_doc._.label = doc._.label

                _init_doc(new_doc, list(zip(*filtered))[1] if filtered else empty_chararray())

                for attr in more_attrs:
                    if attr.endswith('_'):
                        attrname = attr[:-1]
                        vals = doc.to_array(attrname)   # without trailing underscore
                        new_doc.from_array(attrname, vals[mask])
                    else:
                        for v, nt in zip((getattr(t._, attr) for t, _ in filtered), new_doc):
                            setattr(nt._, attr, v)

            new_docs.append(new_doc)

        assert len(docs) == len(new_docs)

        return new_docs
    else:   # simply set the new filter mask to previously unfiltered elements; changes document masks in-place
        for mask, doc in zip(matches, docs):
            assert len(mask) == sum(doc.user_data['mask'])
            doc.user_data['mask'][doc.user_data['mask']] = mask

        return docs


def _init_doc(doc, tokens=None, mask=None):
    assert isinstance(doc, Doc)

    if tokens is None:
        tokens = [t.text for t in doc]
    else:
        assert isinstance(tokens, (list, tuple, np.ndarray))
        assert len(doc) == len(tokens)

    if mask is not None:
        assert isinstance(mask, np.ndarray)
        assert np.issubdtype(mask.dtype, np.bool_)
        assert len(doc) == len(mask)

    if len(tokens) == 0:
        doc.user_data['tokens'] = empty_chararray()
    else:
        doc.user_data['tokens'] = np.array(tokens) if not isinstance(tokens, np.ndarray) else tokens
    doc.user_data['mask'] = mask if isinstance(mask, np.ndarray) else np.repeat(True, len(doc))


def _filtered_doc_arr(lst, doc):
    return np.array(lst)[doc.user_data['mask']]


def _filtered_docs_tokens(docs):
    return list(map(_filtered_doc_tokens, docs))


def _filtered_doc_tokens(doc, as_list=False):
    if isinstance(doc, list):
        return doc
    else:
        res = doc.user_data['tokens'][doc.user_data['mask']]
        return res.tolist() if as_list else res


def _replace_doc_tokens(doc, new_tok):
    # replace all non-filtered tokens
    assert sum(doc.user_data['mask']) == len(new_tok)
    doc.user_data['tokens'][doc.user_data['mask']] = new_tok


def _get_docs_attr(docs, attr_name, custom_attr=True):
    return [getattr(doc._, attr_name) if custom_attr else getattr(doc, attr_name) for doc in docs]


def _get_docs_tokenattrs_keys(docs, default_attrs=None):
    if default_attrs:
        attrs = default_attrs
    else:
        attrs = ['lemma_', 'whitespace_']

    if len(docs) > 0:
        for doc in docs:
            if doc.is_tagged and 'pos_' not in attrs:
                attrs.append('pos_')

            if len(doc) > 0:
                firsttok = next(iter(doc))
                attrs.extend([attr for attr in dir(firsttok._)
                             if attr not in {'has', 'get', 'set'} and not attr.startswith('_')])
                break

    return attrs


def _get_docs_tokenattrs(docs, attr_name, custom_attr=True):
    return [[getattr(t._, attr_name) if custom_attr else getattr(t, attr_name)
             for t, m in zip(doc, doc.user_data['mask']) if m]
            for doc in docs]
