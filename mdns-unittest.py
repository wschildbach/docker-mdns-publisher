"""Unit tests for publishing functionality
"""
import unittest
from contextlib import contextmanager
import tracemalloc
import dockersock_watcher as dw

class TestRegistration(unittest.TestCase):
    """test the registration/publishing functionality
    """
    @classmethod
    def setUpClass(cls):
        tracemalloc.start()

        cls._lhw = dw.LocalHostWatcher(None)
        cls._lhw.__enter__() # pylint: disable=unnecessary-dunder-call

    @classmethod
    def tearDownClass(cls):
        cls._lhw.__exit__(None,None,None)

    @contextmanager
    def assertNotRaises(self, exc_type): # pylint: disable=invalid-name
        """assert that a specific exception is not raised
        """
        try:
            yield None
        except exc_type as e:
            raise self.failureException(f'{exc_type.__name__} raised') from e

    def test_cname(self):
        """test that publishing a normal cname works
        """
        with self.assertLogs() as cm:
            si = self._lhw.publish("foo.local",80,None)
            self._lhw.unpublish("foo.local",80)

        self.assertEqual(cm.output, [
            'INFO:docker-mdns-publisher:publishing foo.local.:80',
            'INFO:docker-mdns-publisher:unpublishing foo.local.:80'
        ])

        self.assertEqual(si.server, "foo.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_cname_twice(self):
        """test that publishing a normal cname works
             twice in a row
        """
        with self.assertLogs() as cm:
            self._lhw.publish("foo.local",80,None)
            self._lhw.unpublish("foo.local",80)
            si = self._lhw.publish("foo.local",80,None)
            self._lhw.unpublish("foo.local",80)

        self.assertEqual(cm.output, [
            'INFO:docker-mdns-publisher:publishing foo.local.:80',
            'INFO:docker-mdns-publisher:unpublishing foo.local.:80',
            'INFO:docker-mdns-publisher:publishing foo.local.:80',
            'INFO:docker-mdns-publisher:unpublishing foo.local.:80'
        ])

        self.assertEqual(si.server, "foo.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_dotted_cname(self):
        """test that publishing a normal cname works
        when it ends with a period
        """
        with self.assertLogs() as cm:
            si = self._lhw.publish("foo.local.",80,None)
            self._lhw.unpublish("foo.local.",80)

        self.assertEqual(cm.output, [
            'INFO:docker-mdns-publisher:publishing foo.local.:80',
            'INFO:docker-mdns-publisher:unpublishing foo.local.:80'
        ])

        self.assertEqual(si.server, "foo.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_cname_with_domain(self):
        """test that publishing a normal cname works
        when it contains a subdomain
        """
        with self.assertLogs() as cm:
            si = self._lhw.publish("foo.subdomain.local",80,None)
            self._lhw.unpublish("foo.subdomain.local",80)

        self.assertEqual(cm.output, [
            'INFO:docker-mdns-publisher:publishing foo.subdomain.local.:80',
            'INFO:docker-mdns-publisher:unpublishing foo.subdomain.local.:80'
        ])

        self.assertEqual(si.server, "foo.subdomain.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_faulty_cname(self):
        """test that publishing a cname in a non-local
        domain throws an error (it doesn't)
        """
#        with dw.LocalHostWatcher(None) as lhw:
#            si = lhw.publish("foo.global.",80,None)
        with self.assertLogs() as cm:
            si = self._lhw.publish("foo.global.",80,None)
            self._lhw.unpublish("foo.global.",80)

        self.assertEqual(cm.output, [
            'INFO:docker-mdns-publisher:publishing foo.global.:80',
            'INFO:docker-mdns-publisher:unpublishing foo.global.:80'
        ])

        self.assertEqual(si.server, "foo.global.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_duplicate_reg(self):
        """test that publishing a normal cname twice
        fails if not unpublished in-between (it doesn't)
        """
        with self.assertLogs() as cm:
            self._lhw.publish("foo.local",80,None)
            si = self._lhw.publish("foo.local",80,None)
            self._lhw.unpublish("foo.local.",80)

        self.assertEqual(cm.output, [
            'INFO:docker-mdns-publisher:publishing foo.local.:80',
            'INFO:docker-mdns-publisher:publishing foo.local.:80',
            'INFO:docker-mdns-publisher:unpublishing foo.local.:80'
        ])

        self.assertEqual(si.server, "foo.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

if __name__ == '__main__':
    unittest.main()
