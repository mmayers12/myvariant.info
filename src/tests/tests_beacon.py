import httplib2
import sys
import os

from biothings.tests.test_helper import BiothingTestHelperMixin
from nose.tools import ok_, eq_

class BeaconTest(BiothingTestHelperMixin):
    __test__ = True # explicitly set this to be a test class

    host = os.getenv("MV_HOST", "")
    host = host.rstrip('/')
    h = httplib2.Http()

    def test_get(self):

        base_query = '/beacon/query?referenceName=12&start=328665&referenceBases=A&alternateBases=G&assemblyId=GRCh37'
        res = self.json_ok(self.get_ok(self.host + base_query))
        res1 = self.json_ok(self.get_ok(self.host + base_query + '&datasetIds=wellderly'))
        res2 = self.json_ok(self.get_ok(self.host + base_query + '&datasetIds=dbsnp'))
        res3 = self.json_ok(self.get_ok(self.host + base_query + '&datasetIds=docm'))
        res4 = self.json_ok(self.get_ok(self.host +
                          base_query.replace('alternateBases=G','alternateBases=T')))

        ok_(res['exists'])
        ok_(res1['exists'])
        ok_(res2['exists'])
        ok_(not res3['exists'])
        ok_(not res4['exists'])

    def test_post(self):
        q = {'referenceName': '12', 'start': '328665', 'referenceBases': 'A',
             'alternateBases': 'G', 'assemblyId': 'GRCh37', }

        res = self.json_ok(self.post_ok(self.host + '/beacon/query', q))

        q['datasetIds'] = 'wellderly'
        res1 = self.json_ok(self.post_ok(self.host + '/beacon/query', q))

        q['datasetIds'] = 'dbsnp'
        res2 = self.json_ok(self.post_ok(self.host + '/beacon/query', q))

        q['datasetIds'] = 'docm'
        res3 = self.json_ok(self.post_ok(self.host + '/beacon/query', q))

        q.pop('datasetIds')
        q['referenceBases'] = 'T'
        res4 = self.json_ok(self.post_ok(self.host + '/beacon/query', q))

        ok_(res['exists'])
        ok_(res1['exists'])
        ok_(res2['exists'])
        ok_(not res3['exists'])
        ok_(not res4['exists'])

