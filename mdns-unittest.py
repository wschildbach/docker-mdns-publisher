import unittest
from contextlib import contextmanager
import tracemalloc
import docker
import dockersock_watcher as dw
import zeroconf as zc

class TestRegistration(unittest.TestCase):
    """
        snapshot1 = tracemalloc.take_snapshot()
        snapshot2 = tracemalloc.take_snapshot()
        top_stats = snapshot2.compare_to(snapshot1, 'lineno')        
        """

    """            
        si2 = zc.ServiceInfo(type_='_http._tcp.local.', name='host1._http._tcp.local.',
        addresses=lhw.interfaces, port=80, weight=0, priority=0,
        server='foo.local.', properties={}, interface_index=None)
        """
        
    @classmethod
    def setUpClass(cls):
        tracemalloc.start()
        
        cls._lhw = dw.LocalHostWatcher(None)
        cls._lhw.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._lhw.__exit__(None,None,None)

    @contextmanager
    def assertNotRaises(self, exc_type):
        try:
            yield None
        except exc_type:
            raise self.failureException('{} raised'.format(exc_type.__name__))

    def test_cname(self):
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
