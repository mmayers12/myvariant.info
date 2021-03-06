import re
import json
from utils.common import dotdict, is_str, is_seq, find_doc
from utils.es import get_es
from elasticsearch import NotFoundError, RequestError
import config

# a query to get variants with most of fields:
# _exists_:dbnsfp AND _exists_:dbsnp AND _exists_:mutdb AND _exists_:cosmic AND _exists_:clinvar AND _exists_:gwassnps

HG38_FIELDS = ['clinvar.hg38', 'dbnsfp.hg38', 'evs.hg38']
HG19_FIELDS = ['clinvar.hg19', 'cosmic.hg19', 'dbnsfp.hg19', 'dbsnp.hg19', 'docm.hg19', 'evs.hg19', 'grasp.hg19'] #, 'mutdb.hg19', 'wellderly.hg19']
CHROM_FIELDS = ['cadd.chrom', 'clinvar.chrom', 'cosmic.chrom', 'dbnsfp.chrom', 'dbsnp.chrom', 'docm.chrom',
                'evs.chrom', 'exac.chrom']#, 'mutdb.chrom', 'wellderly.chrom']


class MVQueryError(Exception):
    pass


class MVScrollSetupError(Exception):
    pass


class ESQuery():
    def __init__(self, index=None, doc_type=None, es_host=None, _use_hg38=False):
        self._es = get_es(es_host)
        self._index = index or config.ES_INDEX_NAME
        self._doc_type = doc_type or config.ES_DOC_TYPE
        self._allowed_options = ['_source', 'start', 'from_', 'size',
                                 'sort', 'explain', 'version', 'facets', 'fetch_all', 'jsonld']  # , 'host']
        self._scroll_time = '1m'
        self._total_scroll_size = 1000   # Total number of hits to return per scroll batch
        self._hg38 = _use_hg38
        self._jsonld = False
        self._context = json.loads(open(config.JSONLD_CONTEXT_PATH, 'r').read())
        if self._total_scroll_size % self.get_number_of_shards() == 0:
            # Total hits per shard per scroll batch
            self._scroll_size = int(self._total_scroll_size / self.get_number_of_shards())
        else:
            raise MVScrollSetupError("_total_scroll_size of {} can't be ".format(self._total_scroll_size) +
                                     "divided evenly among {} shards.".format(self.get_number_of_shards()))

    def _use_hg38(self):
        self._hg38 = True

    def _use_hg19(self):
        self._hg38 = False

    def _get_variantdoc(self, hit):
        doc = hit.get('_source', hit.get('fields', {}))
        doc.setdefault('_id', hit['_id'])
        for attr in ['_score', '_version']:
            if attr in hit:
                doc.setdefault(attr, hit[attr])

        if hit.get('found', None) is False:
            # if found is false, pass that to the doc
            doc['found'] = hit['found']
        # add cadd license info
        if 'cadd' in doc:
            doc['cadd']['_license'] = 'http://goo.gl/bkpNhq'
        if self._jsonld:
            doc = self._insert_jsonld(doc)
        return doc

    def _insert_jsonld(self, k):
        ''' Insert the jsonld links into this document.  Called by _get_variantdoc. '''
        # get the context
        context = self._context

        # set the root
        k.update(context['root'])

        for key in context:
            if key != 'root':
                keys = key.split('/')
                try:
                    doc = find_doc(k, keys)
                    if type(doc) == list:
                        for _d in doc:
                            _d.update(context[key])
                    elif type(doc) == dict:
                        doc.update(context[key])
                    else:
                        continue
                        #print('error')
                except:
                    continue
                    #print('keyerror')
        return k

    def _cleaned_res(self, res, empty=[], error={'error': True}, single_hit=False):
        '''res is the dictionary returned from a query.
           do some reformating of raw ES results before returning.

           This method is used for self.mget_variants2 and self.get_variant2 method.
        '''
        if 'error' in res:
            return error

        hits = res['hits']
        total = hits['total']
        if total == 0:
            return empty
        elif total == 1 and single_hit:
            return self._get_variantdoc(hits['hits'][0])
        else:
            return [self._get_variantdoc(hit) for hit in hits['hits']]

    def _clean_res2(self, res):
        '''res is the dictionary returned from a query.
           do some reformating of raw ES results before returning.

           This method is used for self.query method.
        '''
        _res = res['hits']
        for attr in ['took', 'facets', 'aggregations', '_scroll_id']:
            if attr in res:
                _res[attr] = res[attr]
        _res['hits'] = [self._get_variantdoc(hit) for hit in _res['hits']]
        return _res

    def _cleaned_scopes(self, scopes):
        '''return a cleaned scopes parameter.
            should be either a string or a list of scope fields.
        '''
        if scopes:
            if is_str(scopes):
                scopes = [x.strip() for x in scopes.split(',')]
            if is_seq(scopes):
                scopes = [x for x in scopes if x]
                if len(scopes) == 1:
                    scopes = scopes[0]
            else:
                scopes = None
        else:
            scopes = None
        return scopes

    def _cleaned_fields(self, fields):
        '''return a cleaned fields parameter.
            should be either None (return all fields) or a list fields.
        '''
        if fields:
            if is_str(fields):
                if fields.lower() == 'all':
                    fields = None     # all fields will be returned.
                else:
                    fields = [x.strip() for x in fields.split(',')]
        else:
            fields = self._default_fields
        return fields

    def _parse_sort_option(self, options):
        sort = options.get('sort', None)
        if sort:
            _sort_array = []
            for field in sort.split(','):
                field = field.strip()
                # if field == 'name' or field[1:] == 'name':
                #     # sorting on "name" field is ignored, as it is a multi-text field.
                #     continue
                if field.startswith('-'):
                    _f = "%s:desc" % field[1:]
                else:
                    _f = "%s:asc" % field
                _sort_array.append(_f)
            options["sort"] = ','.join(_sort_array)
        return options

    def _get_cleaned_query_options(self, kwargs):
        """common helper for processing fields, kwargs and other options passed to ESQueryBuilder."""
        options = dotdict()
        options.raw = kwargs.pop('raw', False)
        options.rawquery = kwargs.pop('rawquery', False)
        options.fetch_all = kwargs.pop('fetch_all', False)
        options.jsonld = kwargs.pop('jsonld', False)
        self._jsonld = options.jsonld
        options.host = kwargs.pop('host', 'myvariant.info')
        scopes = kwargs.pop('scopes', None)
        if scopes:
            options.scopes = self._cleaned_scopes(scopes)
        fields = kwargs.pop('fields', None)
        if fields:
            fields = self._cleaned_fields(fields)
            if fields:
                kwargs["_source"] = fields
        kwargs = self._parse_sort_option(kwargs)
        for key in set(kwargs) - set(self._allowed_options):
            del kwargs[key]
        options.kwargs = kwargs
        return options

    def get_number_of_shards(self):
        r = self._es.indices.get_settings(self._index)
        n_shards = r[list(r.keys())[0]]['settings']['index']['number_of_shards']
        n_shards = int(n_shards)
        return n_shards

    def exists(self, vid):
        """return True/False if a variant id exists or not."""
        try:
            doc = self.get_variant(vid, fields=None)
            return doc['found']
        except NotFoundError:
            return False

    def get_variant(self, vid, **kwargs):
        '''unknown vid return None'''
        options = self._get_cleaned_query_options(kwargs)
        kwargs = {"_source": options.kwargs["_source"]} if "_source" in options.kwargs else {}
        try:
            res = self._es.get(index=self._index, id=vid, doc_type=self._doc_type, **kwargs)
        except NotFoundError:
            return

        if options.raw:
            return res

        res = self._get_variantdoc(res)
        return res

    def mget_variants(self, vid_list, **kwargs):
        options = self._get_cleaned_query_options(kwargs)
        kwargs = {"_source": options.kwargs["_source"]} if "_source" in options.kwargs else {}
        res = self._es.mget(body={'ids': vid_list}, index=self._index, doc_type=self._doc_type, **kwargs)
        return res if options.raw else [self._get_variantdoc(doc) for doc in res['docs']]

    def get_variant2(self, vid, **kwargs):
        options = self._get_cleaned_query_options(kwargs)
        qbdr = ESQueryBuilder(**options.kwargs)
        _q = qbdr.build_id_query(vid, options.scopes)
        if options.rawquery:
            return _q
        res = self._es.search(body=_q, index=self._index, doc_type=self._doc_type)
        if not options.raw:
            res = self._cleaned_res(res, empty=None, single_hit=True)
        return res

    def mget_variants2(self, vid_list, **kwargs):
        '''for /query post request'''
        options = self._get_cleaned_query_options(kwargs)
        qbdr = ESQueryBuilder(**options.kwargs)
        try:
            _q = qbdr.build_multiple_id_query(vid_list, scopes=options.scopes)
        except MVQueryError as err:
            return {'success': False,
                    'error': err.message}
        if options.rawquery:
            return _q
        res = self._es.msearch(body=_q, index=self._index, doc_type=self._doc_type)['responses']
        if options.raw:
            return res

        assert len(res) == len(vid_list)
        _res = []

        for i in range(len(res)):
            hits = res[i]
            qterm = vid_list[i]
            hits = self._cleaned_res(hits, empty=[], single_hit=False)
            if len(hits) == 0:
                _res.append({u'query': qterm,
                             u'notfound': True})
            elif 'error' in hits:
                _res.append({u'query': qterm,
                             u'error': True})
            else:
                for hit in hits:
                    hit[u'query'] = qterm
                    _res.append(hit)
        return _res

    def query(self, q, **kwargs):
        # Check if special interval query pattern exists
        interval_query = self._parse_interval_query(q)
        facets = self._parse_facets_option(kwargs)
        options = self._get_cleaned_query_options(kwargs)
        scroll_options = {}
        if options.fetch_all:
            scroll_options.update({'search_type': 'scan', 'size': self._scroll_size, 'scroll': self._scroll_time})
        options['kwargs'].update(scroll_options)
        qbdr = ESQueryBuilder(**options.kwargs)
        if interval_query:
            _query = qbdr.build_interval_query(chr=interval_query["chr"],
                                               gstart=interval_query["gstart"],
                                               gend=interval_query["gend"],
                                               rquery=interval_query["query"],
                                               hg38=self._hg38, **options['kwargs'])
        else:
            _query = qbdr.build_default_query(q=q, facets=facets)

        if options.rawquery:
            return _query

        try:
            res = self._es.search(index=self._index, doc_type=self._doc_type, body=_query, **options.kwargs)
        except RequestError:
            return {"error": "invalid query term.", "success": False}

        if not options.raw:
            res = self._clean_res2(res)
        return res

    def scroll(self, scroll_id, **kwargs):
        '''return the results from a scroll ID, recognizes options.raw'''
        options = self._get_cleaned_query_options(kwargs)
        r = self._es.scroll(scroll_id, scroll=self._scroll_time)
        scroll_id = r.get('_scroll_id')
        if scroll_id is None or not r['hits']['hits']:
            return {'success': False, 'error': 'No results to return.'}
        else:
            if not options.raw:
                res = self._clean_res2(r)
            # res.update({'_scroll_id': scroll_id})
            if r['_shards']['failed']:
                res.update({'_warning': 'Scroll request has failed on {} shards out of {}.'.format(r['_shards']['failed'], r['_shards']['total'])})
        return res

    def _parse_facets_option(self, kwargs):
        facets = kwargs.pop('facets', None)
        if facets:
            _facets = {}
            for field in facets.split(','):
                _facets[field] = {"terms": {"field": field}}
            return _facets

    def _parse_interval_query(self, q):
        interval_pattern = r'(?P<pre_query>.+(?P<pre_and>[Aa][Nn][Dd]))*(?P<interval>\s*chr(?P<chr>\w+):(?P<gstart>[0-9,]+)-(?P<gend>[0-9,]+)\s*)(?P<post_query>(?P<post_and>[Aa][Nn][Dd]).+)*'
        single_pattern = r'(?P<pre_query>.+(?P<pre_and>[Aa][Nn][Dd]))*(?P<interval>\s*chr(?P<chr>\w+):(?P<gend>(?P<gstart>[0-9,]+))\s*)(?P<post_query>(?P<post_and>[Aa][Nn][Dd]).+)*'
        patterns = [interval_pattern, single_pattern]
        if q:
            for pattern in patterns:
                mat = re.search(pattern, q)
                if mat:
                    r = mat.groupdict()
                    if r['pre_query']:
                        r['query'] = r['pre_query'].rstrip(r['pre_and']).rstrip()
                        if r['post_query']:
                            r['query'] += ' ' + r['post_query']
                    elif r['post_query']:
                        r['query'] = r['post_query'].lstrip(r['post_and']).lstrip()
                    else:
                        r['query'] = None
                    return r

    def query_fields(self, **kwargs):
        # query the metadata to get the available fields for a variant object
        r = self._es.indices.get(index=self._index)
        return r[list(r.keys())[0]]['mappings']['variant']['properties']

    def get_mapping_meta(self):
        """return the current _meta field."""
        m = self._es.indices.get_mapping(index=self._index, doc_type=self._doc_type)
        m = m[self._index]['mappings'][self._doc_type]
        return m.get('_meta', {})


class ESQueryBuilder:
    def __init__(self, **query_options):
        self._query_options = query_options

    def _get_genome_position_fields(self, hg38=False):
        if hg38:
            try:
                return config.HG38_FIELDS
            except AttributeError:
                return HG38_FIELDS
        else:
            try:
                return config.HG19_FIELDS
            except AttributeError:
                return HG19_FIELDS

    def _get_chrom_fields(self):
        try:
            return config.CHROM_FIELDS
        except AttributeError:
            return CHROM_FIELDS

    def build_id_query(self, vid, scopes=None):
        _default_scopes = '_id'
        scopes = scopes or _default_scopes
        if is_str(scopes):
            _query = {
                "match": {
                    scopes: {
                        "query": "{}".format(vid),
                        "operator": "and"
                    }
                }
            }
        elif is_seq(scopes):
            _query = {
                "multi_match": {
                    "query": "{}".format(vid),
                    "fields": scopes,
                    "operator": "and"
                }
            }
        else:
            raise ValueError('"scopes" cannot be "%s" type'.format(type(scopes)))
        _q = {"query": _query}
        self._query_options.pop("query", None)    # avoid "query" be overwritten by self.query_options
        _q.update(self._query_options)
        return _q

    def build_multiple_id_query(self, vid_list, scopes=None):
        """make a query body for msearch query."""
        _q = []
        for id in vid_list:
            _q.extend(['{}', json.dumps(self.build_id_query(id, scopes))])
        _q.append('')
        return '\n'.join(_q)

    def build_default_query(self, q, facets=None):
        """ Default query for request to /query endpoint - called by the ESQuery.query method. """
        _query = {
            "query": {
                "query_string": {
                    "query": q
                }
            }
        }
        if facets:
            _query['facets'] = facets
        return _query

    def build_interval_query(self, chr, gstart, gend, rquery, hg38, **kwargs):
        """ Build an interval query - called by the ESQuery.query method. """
        if chr.lower().startswith('chr'):
            chr = chr[3:]

        # ES 1.x query
        #_query = {
        #    "query": {
        #        "filtered": {
        #            "filter": {
        #                "bool": {
        #                    "must": [{
        #                        "bool": {
        #                            "should": [{
        #                                "term": {field: chr.lower()}
        #                            } for field in self._get_chrom_fields()]
        #                        }
        #                    }, {
        #                        "bool": {
        #                            "should": [{
        #                                "bool": {
        #                                    "must": [
        #                                        {
        #                                            "range": {field + ".start": {"lte": gend}}
        #                                        },
        #                                        {
        #                                            "range": {field + ".end": {"gte": gstart}}
        #                                        }
        #                                    ]
        #                                }
        #                            } for field in self._get_genome_position_fields(hg38)]
        #                        }
        #                    }]
        #                }
        #            }
        #        }
        #    }
        #}
        # ES 2.x query
        _query = {
            "query": {
                "bool": {
                    "filter": {
                        "bool": {
                            "must": [{
                                "bool": {
                                    "should": [{
                                        "term": {field: chr.lower()}
                                    } for field in self._get_chrom_fields()]
                                }
                            }, {
                                "bool": {
                                    "should": [{
                                        "bool": {
                                            "must": [
                                                {
                                                    "range": {field + ".start": {"lte": gend}}
                                                },
                                                {
                                                    "range": {field + ".end": {"gte": gstart}}
                                                }
                                            ]
                                        }
                                    } for field in self._get_genome_position_fields(hg38)]
                                }
                            }]
                        }
                    }
                }
            }
        }
        if rquery:
            _query["query"]["bool"]["must"] = {"query_string": {"query": rquery}}
        return _query
