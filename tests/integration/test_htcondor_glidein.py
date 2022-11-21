from __future__ import absolute_import, division, print_function

import htcondor
import os
import sys
import time
import unittest
import urllib
import tarfile
import glob
import tempfile
import requests

# TODO: Install pyglidein egg instead of appending paths
sys.path.append('/pyglidein')
from pyglidein.config import Config

CONFIGURATION = '/pyglidein/dev_build/client_condor/root/etc/sv/pyglidein_client/htcondor_config'
SERVER_CONFIGURATION = os.path.join('/pyglidein/dev_build/server/root/etc/sv/pyglidein_server/',
                                    'pyglidein_server.config')
SECRETS = '/home/condor/.pyglidein_secrets'


class TestHTCondorGlidein(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        config_dict = Config(CONFIGURATION)
        server_config_dict = Config(SERVER_CONFIGURATION)
        secrets_dict = Config(SECRETS)
        cls.config = config_dict
        cls.server_config = server_config_dict
        cls.secrets = secrets_dict

        cls.glidein_site = config_dict['Glidein']['site']
        cls.minio_url = config_dict['StartdLogging']['url']
        cls.minio_bucket = config_dict['StartdLogging']['bucket']
        cls.minio_acces_key = secrets_dict['StartdLogging']['access_key']
        cls.minio_secret_key = secrets_dict['StartdLogging']['secret_key']
        cls.minio_secure = True
        cls.pyglidein_client_name = 'pyglidein-client'
        cls.metrics_graphite_server = server_config_dict['metrics']['graphite_server']
        cls.metrics_namespace = server_config_dict['metrics']['namespace']

        cls.tmpdir = tempfile.mkdtemp()

    def setUp(self):
        os.chdir(self.tmpdir)

    def test_glidein_startd(self):

        # Submitting some sleep jobs
        job = {"executable": "/bin/sleep",
               "arguments": "5m",
               "request_memory": "500"}

        sub = htcondor.Submit(job)
        schedd = htcondor.Schedd()
        with schedd.transaction() as txn:
            sub.queue(txn, 8)

        # Waiting for the glideins to start
        time.sleep(60)

        coll = htcondor.Collector()
        startds = coll.locateAll(htcondor.DaemonTypes.Startd)

        self.assertTrue(len(startds) > 0,
                        msg='No STARTDs found.')
        for startd in startds:
            self.assertTrue(
                startd['GLIDEIN_Site'] == self.glidein_site,
                msg='GLIDEIN_Site CLASSAD: {} not equal to {}'.format(
                    startd['GLIDEIN_Site'], self.glidein_site))

    def test_submit_hello_world(self):

        output_file = "test_submit_hello_world_out"
        output_text = "hello pyglidein"
        job = {"executable": "/bin/echo",
               "arguments": output_text,
               "output": output_file,
               "request_memory": "500"}

        sub = htcondor.Submit(job)
        schedd = htcondor.Schedd()
        with schedd.transaction() as txn:
            cluster_id = sub.queue(txn)

        # Waiting for job to complete
        for i in xrange(0, 5):
            history = schedd.history('ClusterId=={}'.format(cluster_id),
                                     ['ClusterId', 'JobStatus'], 1)
            if sum(1 for _ in history) == 0:
                time.sleep(30)
            else:
                break

        self.assertTrue(os.path.exists(output_file),
                        msg="Output file doesn't exist.")

        with open(output_file, 'r') as f:
            data = f.readlines()[0].rstrip()
        self.assertEqual(data, output_text,
                         msg='Output File Text: {} not equal to {}'.format(data, output_text))

    def test_logging(self):

        # Submitting some sleep jobs
        job = {"executable": "/bin/sleep",
               "arguments": "5m",
               "request_memory": "500"}

        sub = htcondor.Submit(job)
        schedd = htcondor.Schedd()
        with schedd.transaction() as txn:
            sub.queue(txn, 1)

        # Waiting for the glideins to start
        time.sleep(60)

        coll = htcondor.Collector()
        startd = coll.locateAll(htcondor.DaemonTypes.Startd)[0]

        url = startd['PRESIGNED_GET_URL']
        log_filename = 'logfile.tar.gz'
        logfile_opener = urllib.URLopener()
        logfile_opener.retrieve(url, log_filename)
        with tarfile.open(log_filename, 'r:gz') as tar:
            def is_within_directory(directory, target):
                
                abs_directory = os.path.abspath(directory)
                abs_target = os.path.abspath(target)
            
                prefix = os.path.commonprefix([abs_directory, abs_target])
                
                return prefix == abs_directory
            
            def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
            
                for member in tar.getmembers():
                    member_path = os.path.join(path, member.name)
                    if not is_within_directory(path, member_path):
                        raise Exception("Attempted Path Traversal in Tar File")
            
                tar.extractall(path, members, numeric_owner=numeric_owner) 
                
            
            safe_extract(tar)
        logdir = glob.glob('log.*')[0]
        self.assertTrue(os.path.exists(os.path.join(logdir, 'MasterLog')),
                        msg='Failed to download logfile: {}'.format(url))

    def test_startd_checks(self):

        startd_resources = ['PYGLIDEIN_RESOURCE_GPU',
                            'PYGLIDEIN_RESOURCE_CVMFS',
                            'PYGLIDEIN_RESOURCE_GRIDFTP']
        startd_metrics = ['PYGLIDEIN_METRIC_TIME_PER_PHOTON']

        coll = htcondor.Collector()
        startd = coll.locateAll(htcondor.DaemonTypes.Startd)
        if len(startd) == 0:
            # Submitting some sleep jobs
            job = {"executable": "/bin/sleep",
                   "arguments": "5m",
                   "request_memory": "500"}

            sub = htcondor.Submit(job)
            schedd = htcondor.Schedd()
            with schedd.transaction() as txn:
                sub.queue(txn, 1)

            # Waiting for the glideins to start
            time.sleep(60)

        startd = coll.locateAll(htcondor.DaemonTypes.Startd)[0]

        for resource in startd_resources:
            self.assertTrue(startd.get(resource, False),
                            msg='{} does not exist or equals False'.format(resource))

        for metric in startd_metrics:
            self.assertTrue(startd.get(metric, 0) > 0,
                            msg='{} does not exist or equals 0'.format(metric))

    def test_client_metrics(self):

        coll = htcondor.Collector()
        startd = coll.locateAll(htcondor.DaemonTypes.Startd)
        if len(startd) == 0:
            # Submitting some sleep jobs
            job = {"executable": "/bin/sleep",
                   "arguments": "5m",
                   "request_memory": "500"}

            sub = htcondor.Submit(job)
            schedd = htcondor.Schedd()
            with schedd.transaction() as txn:
                sub.queue(txn, 1)

            # Waiting for the glideins to start
            time.sleep(60)

        uuid = 'pyglideinpyglideinclient'
        partition = 'Cluster'
        metrics = [
            'glideins.launched',
            'glideins.running',
            'glideins.idle',
            'glideins.avg_idle_time',
            'glideins.min_idle_time',
            'glideins.max_idle_time'
        ]
        for metric in metrics:
            path = '.'.join([self.metrics_namespace, uuid, partition, metric])
            url = 'http://{}/render?target={}'.format(self.metrics_graphite_server, path)
            url += '&format=json&from=-5min'
            r = requests.get(url)
            output = r.json()
            self.assertTrue(len(output) > 0,
                            msg='{} client metric not found'.format(path))
            if len(output) > 0:
                output = output[0]
                self.assertTrue(len(output['datapoints']) > 0,
                                msg='No datapoints found for {}.'.format(path))
                self.assertTrue(output['tags']['name'] == path,
                                msg='Metrics mismatch for {}.'.format(path))
                not_zeros = False
                for datapoint in output['datapoints']:
                    if datapoint[0] != 0.0:
                        not_zeros = True
                self.assertTrue(not_zeros, msg='Add datapoints are zero for {}.'.format(path))

    def tearDown(self):

        schedd = htcondor.Schedd()
        schedd.act(htcondor.JobAction.Remove, 'true')


if __name__ == '__main__':
    unittest.main()
