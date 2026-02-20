"""Unit tests for publishing functionality
"""
import unittest
import os
from unittest import mock
from contextlib import contextmanager
import dockersock_watcher as dw
from utils import IgnoredError,FatalError

VERBOSE=False
def dbg_print(e):
    """only used during development."""
    if VERBOSE:
        print(e)

class TestEnviron(unittest.TestCase):
    """test all environment variable settings
    """

    def test_adapters(self):
        """test whether the ADAPTERS environment variable
        fails for non-existent interfaces"""

        with mock.patch.dict(os.environ, {"ADAPTERS": "non-existent"}):
            with self.assertRaises(FatalError):
                lhw = dw.LocalHostWatcher(None)
                lhw.__enter__() # pylint: disable=unnecessary-dunder-call

class TestRegistration(unittest.TestCase):
    """test the registration/publishing functionality
    """

    @classmethod
    def setUpClass(cls):

        # use the loopback interface only, to not upset the network
        with mock.patch.dict(os.environ, {"ADAPTERS": "lo"}):
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

    def test_register_server_only(self):
        """register foo.local:80"""
        with self.assertLogs():
            si = self._lhw.publish("foo.local",80)
            self._lhw.unpublish(si)

        self.assertEqual(si.server, "foo.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_pub_unpub_pub(self):
        """If we publish, then unpublish, can we publish again?"""
        with self.assertLogs():
            si = self._lhw.publish("foo.local",80)
            self._lhw.unpublish(si)
            si = self._lhw.publish("foo.local",80)
            self._lhw.unpublish(si)

        self.assertEqual(si.server, "foo.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_dotted_cname(self):
        """register foo.local.:80 (ending with period)"""
        with self.assertLogs():
            si = self._lhw.publish("foo.local.",80)
            self._lhw.unpublish(si)

        self.assertEqual(si.server, "foo.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_cname_with_domain(self):
        """register foo.subdomain.local.:80"""
        with self.assertLogs():
            si = self._lhw.publish("foo.subdomain.local",80)
            self._lhw.unpublish(si)

        self.assertEqual(si.server, "foo.subdomain.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_cname_with_spaces(self):
        """register "foo 1".local.:80"""
        with self.assertLogs():
            with self.assertRaises(IgnoredError) as e:
                si = self._lhw.publish("foo 1.local",80)
                self._lhw.unpublish(si)
            dbg_print(f"EXPECTED: {e.exception}")

    def test_cname_with_umlaut(self):
        """register öfoo.local.:80"""
        with self.assertLogs():
            si = self._lhw.publish("öfoo.local",80)
            self._lhw.unpublish(si)
        dbg_print(si)

    def test_non_local_domain(self):
        """publish foo.global.:80"""

        with self.assertLogs():
            with self.assertRaises(IgnoredError) as e:
                si = self._lhw.publish("foo.global.",80)
                self._lhw.unpublish(si)
            dbg_print(f"EXPECTED: {e.exception}")

    def test_duplicate_registration(self):
        """Expect a failure publishing twice in a row"""
        with self.assertLogs():
            si = self._lhw.publish("foo.local",80)
            with self.assertRaises(IgnoredError) as e:
                self._lhw.publish("foo.local",80)
            dbg_print(f"EXPECTED: {e.exception}")

            self._lhw.unpublish(si)

        self.assertEqual(si.server, "foo.local.")
        self.assertEqual(si.port, 80)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_known_port_raises_no_exception(self):
        """A known port is supplied"""

        with self.assertLogs():
            si = self._lhw.publish("foo.local",1883)
            self._lhw.unpublish(si)

        self.assertEqual(si.port, 1883)
        self.assertEqual(si.type,"_mqtt._tcp.local.")

    def test_unknown_port_raises_exception(self):
        """Expect a failure if an unknown port is supplied, but no explicit service"""

        with self.assertLogs():
            with self.assertRaises(IgnoredError) as e:
                self._lhw.publish("foo.local",6789)
            dbg_print(f"EXPECTED: {e.exception}")

    def test_servicetype_supplied(self):
        """Supply an unknown port together with a service"""

        with self.assertLogs():
            si = self._lhw.publish("foo.local",6789,"_http._tcp")
            self._lhw.unpublish(si)

        self.assertEqual(si.port, 6789)
        self.assertEqual(si.type,"_http._tcp.local.")

    def test_servicetype_with_subtype(self):
        """Supply a service type with subtype"""

        with self.assertLogs():
            si = self._lhw.publish("foo.local",80,"rest._sub._http._tcp")
            self._lhw.unpublish(si)

        self.assertEqual(si.type,"rest._sub._http._tcp.local.")

    def test_txt_record_kvpair(self):
        """publish txt record with value"""

        with self.assertLogs():
            si = self._lhw.publish("foo.local",6789,"_http._tcp",{"i_am_a_kvpair":"yes"})
            self._lhw.unpublish(si)

        self.assertEqual(si.properties,{b'i_am_a_kvpair': b'yes'})

    def test_txt_record_bool(self):
        """publish txt record without value"""

        with self.assertLogs():
            si = self._lhw.publish("foo.local",6789,"_http._tcp",{"i_am_a_bool":None})
            self._lhw.unpublish(si)

        self.assertEqual(si.properties,{b'i_am_a_bool': None})

if __name__ == '__main__':
    unittest.main()
