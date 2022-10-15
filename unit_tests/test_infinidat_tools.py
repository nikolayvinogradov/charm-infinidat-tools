# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import subprocess
import unittest
from io import StringIO
from unittest import mock
from src.charm import InfinidatToolsCharm
from ops.testing import Harness

from ops.model import (
    ActiveStatus,
    BlockedStatus,
)

from charmhelpers.core.host_factory.ubuntu import UBUNTU_RELEASES

SOURCE = "deb https://repo.infinidat.com/packages/main-stable/apt/linux-ubuntu focal main"  # noqa: E501
KEY = """\
-----BEGIN PGP PUBLIC KEY BLOCK-----
Version: GnuPG v1.4.11 (GNU/Linux)

mQENBFESDRIBCADMR7MQMbH4GdCQqfrOMt35MhBwwH4wv9kb1WRSTxa0CmuzYaBB
1nJ0nLaMAwHsEr9CytPWDpMngm/3nt+4F2hJcsOEkQkqeJ31gScJewM+AOUV3DEl
qOeXXYLcP+jUY6pPjlZpOw0p7moUQPXHn+7amVrk7cXGQ8O3B+5a5wjN86LT2hlX
DlBlV5bX/DYluiPUbvQLOknmwO53KpaeDeZc4a8iIOCYWu2ntuAMddBkTps0El5n
JJZMTf6os2ZzngWMZRMDiVJgqVRi2b+8SgFQlQy0cAmne/mpgPrRq0ZMX3DokGG5
hnIg1mF82laTxd+9qtiOxupzJqf8mncQHdaTABEBAAG0IWFwcF9yZXBvIChDb21t
ZW50KSA8bm9AZW1haWwuY29tPokBOAQTAQIAIgUCURINEgIbLwYLCQgHAwIGFQgC
CQoLBBYCAwECHgECF4AACgkQem2D/j05RYSrcggAsCc4KppV/SZX5XI/CWFXIAXw
+HaNsh2EwYKf9DhtoGbTOuwePvrPGcgFYM3Tu+m+rziPnnFl0bs0xwQyNEVQ9yDw
t465pSgmXwEHbBkoISV1e4WYtZAsnTNne9ieJ49Ob/WY4w3AkdPRK/41UP5Ct6lR
HHRXrSWJYHVq5Rh6BakRuMJyJLz/KvcJAaPkA4U6VrPD7PFtSecMTaONPjGCcomq
b7q84G5ZfeJWb742PWBTS8fJdC+Jd4y5fFdJS9fQwIo52Ff9In2QBpJt5Wdc02SI
fvQnuh37D2P8OcIfMxMfoFXpAMWjrMYc5veyQY1GXD/EOkfjjLne6qWPLfNojA==
=w5Os
-----END PGP PUBLIC KEY BLOCK-----
"""


class TestInfinidatToolsCharm(unittest.TestCase):

    def setUp(self):
        self.harness = Harness(InfinidatToolsCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.harness.set_leader(True)
        backend = self.harness.add_relation('juju-info', 'nova')
        self.harness.add_relation_unit(backend, 'infinidat-tools/0')

    def _get_source(self, codename, pocket, baseurl=None):
        if baseurl is None:
            baseurl = self.harness.charm.DEFAULT_REPO_BASEURL
        return ' '.join((
            'deb',
            baseurl,
            codename,
            pocket))

    @mock.patch('src.charm.add_source')
    @mock.patch('src.charm.apt_update')
    @mock.patch('src.charm.apt_install')
    @mock.patch('src.charm.lsb_release')
    @mock.patch('src.charm.InfinidatToolsCharm._get_default_repo_key')
    @mock.patch('src.charm.InfinidatToolsCharm._set_lvm_conf_global_filter')
    @mock.patch('src.charm.InfinidatToolsCharm._run_infinihost_check')
    @mock.patch('src.charm.InfinidatToolsCharm._update_multipath_conf')
    @mock.patch('src.charm.InfinidatToolsCharm._regenerate_initrd')
    def test_repo_management(self, _regenerate_initrd, _update_multipath_conf,
                             _run_infinihost_check,
                             _set_lvm_conf_global_filter,
                             _get_default_repo_key, lsb_release, apt_install,
                             apt_update, add_source):

        dynamic_source = self._get_source('{distrib_codename}', 'main')

        # generate test data for both 'source' values that need substituion
        # and for the static ones

        test_data = []

        for release in UBUNTU_RELEASES:
            static_source = self._get_source(release, 'main')
            test_data.append(
                (dynamic_source, release,
                    self._get_source(release, 'main')),
            )
            test_data.append(
                (static_source, release, static_source),
            )

        for i in test_data:
            # distro codename the charm runs on
            lsb_release.return_value = {'DISTRIB_CODENAME': i[1]}

            # configure to use specific repo version
            self.harness.update_config({
                'install_sources': i[0],
                'install_keys': KEY,
            })

            self.harness.charm.on.install.emit()

            # make sure the repo management calls were correct
            add_source.assert_called_with(i[2], KEY)
            apt_install.assert_called_with(self.harness.charm.PACKAGES,
                                           fatal=True)

    @mock.patch('subprocess.Popen')
    @mock.patch('subprocess.Popen.communicate')
    @mock.patch('subprocess.Popen.wait')
    def test_infinibox_settings_check(self, pwait, pcomm, popen):
        """
        Test basic functionality of _run_infinihost:
        1. Check that method properly handles absense of infinihost
        2. Check subprocess.PIPE
        3. Check redirect to file
        4. Check return code 0 1 2
        """
        with open('unit_tests/infinibox-sample.txt') as f:
            data = f.read()

        pcomm.return_value = (data, '')
        pwait.return_value = 2

        class p:
            def __init__(self):
                self.communicate = pcomm
                self.wait = pwait

        popen.return_value = p()

        code = self.harness.charm.\
            _run_infinihost_check(auto_fix=False, stdout=subprocess.PIPE)
        pwait.assert_called_with()
        self.assertEqual(code, 2)

        # test redirection to a file
        f = '123'
        self.harness.charm._run_infinihost_check(auto_fix=True, stdout=f)
        popen.assert_called_with([
            'infinihost', 'settings', 'check', '--auto-fix'],
            stdout=f, shell=mock.ANY, env=mock.ANY)

        # make sure the missing infinihost is handled with re-raise
        popen.side_effect = FileNotFoundError(
            "[Errno 2] No such file or directory: 'infinihost'"
        )
        self.assertRaises(FileNotFoundError,
                          self.harness.charm._run_infinihost_check)

    @mock.patch('src.charm.add_source')
    @mock.patch('src.charm.apt_update')
    @mock.patch('src.charm.apt_install')
    @mock.patch('src.charm.lsb_release')
    @mock.patch('src.charm.InfinidatToolsCharm._get_default_repo_key')
    @mock.patch('src.charm.InfinidatToolsCharm._set_lvm_conf_global_filter')
    @mock.patch('src.charm.InfinidatToolsCharm._run_infinihost_check')
    @mock.patch('src.charm.InfinidatToolsCharm._update_multipath_conf')
    @mock.patch('src.charm.InfinidatToolsCharm._regenerate_initrd')
    def test_default_gpg_key(self, _regenerate_initrd, _update_multipath_conf,
                             _run_infinihost_check,
                             _set_lvm_conf_global_filter,
                             _get_default_repo_key, lsb_release,
                             apt_install, apt_update, add_source):
        _get_default_repo_key.return_value = KEY
        self.harness.update_config({
            'install_sources': self._get_source('focal', 'main')
        })
        self.harness.charm.on.install.emit()
        add_source.assert_called_with(self._get_source('focal', 'main'), KEY)

    def test_multipath_config_patching(self):
        pass

    @mock.patch('src.charm.InfinidatToolsCharm._set_lvm_conf_global_filter')
    def test_on_config_changed(self, _set_lvm_conf_global_filter):
        """
        Make sure that LVM configuration update is called
        in on_config() handler
        """
        self.harness.update_config({
            'install_sources': self._get_source('focal', 'main')
        })
        _set_lvm_conf_global_filter.assert_called_with(
            self.harness.model.config.get('lvm_global_filter'))

        self.assertTrue(isinstance(
            self.harness.model.unit.status, ActiveStatus
        ))

    @mock.patch('src.charm.add_source')
    @mock.patch('src.charm.apt_update')
    @mock.patch('src.charm.apt_install')
    @mock.patch('src.charm.lsb_release')
    @mock.patch('src.charm.InfinidatToolsCharm._get_default_repo_key')
    @mock.patch('src.charm.InfinidatToolsCharm._set_lvm_conf_global_filter')
    @mock.patch('src.charm.InfinidatToolsCharm._run_infinihost_check')
    @mock.patch('src.charm.InfinidatToolsCharm._update_multipath_conf')
    @mock.patch('src.charm.InfinidatToolsCharm._regenerate_initrd')
    def test_on_install_handler(self, _regenerate_initrd,
                                _update_multipath_conf,
                                _run_infinihost_check,
                                _set_lvm_conf_global_filter,
                                _get_default_repo_key, lsb_release,
                                apt_install, apt_update, add_source):
        """
        Make sure on_install() handler does all necessary stuff:
        1. Calls infinihost with autofix
        2. Updates multipath config
        3. Sets lvm_global_filter
        4. Sets status to Active if ok
        5. Sets status to Blocked if it fails
        """
        self.harness.update_config({
            'install_sources': self._get_source('focal', 'main')
        })
        self.harness.charm.on.install.emit()

        _run_infinihost_check.assert_called_with(auto_fix=True)
        _update_multipath_conf.assert_called_with()
        _set_lvm_conf_global_filter.assert_called_with(
            self.harness.model.config.get('lvm_global_filter'))

        self.assertTrue(isinstance(
            self.harness.model.unit.status, ActiveStatus
        ))
        _run_infinihost_check.side_effect = FileNotFoundError(
            "[Errno 2] No such file or directory: 'infinihost'"
        )

        self.harness.charm.on.install.emit()

        self.assertTrue(isinstance(
            self.harness.model.unit.status, BlockedStatus
        ))

    @mock.patch('builtins.open')
    def test_lvm_config_patching(self, _open):

        tpl = r"""# Configuration option devices/global_filter.
# Limit the block devices that are used by LVM system components.
# Because devices/filter may be overridden from the command line, it is
# not suitable for system-wide device filtering, e.g. udev.
# Use global_filter to hide devices from these LVM system components.
# The syntax is the same as devices/filter. Devices rejected by
# global_filter are not opened by LVM.
# This configuration option has an automatic default value.
# global_filter = [ "a|.*|" ]{0}
# other contents
"""
        input_data = tpl.format('')

        value = '[ "a|^/dev/sd.*|", "a|^/dev/vd.*|", "r|.*|" ]'
        updated_data = tpl.format(
            '\nglobal_filter = ' + value +
            ' # __infinidat_tools__'
        )

        self.maxDiff = 1000
        updated_data2 = tpl.format(
            '\nglobal_filter = ' + value +
            ' 2 # __infinidat_tools__'
        )

        class fake_fh:
            def __init__(self, expected):
                self.buf = StringIO(expected)
                self.newbuf = StringIO('')
                self.readlines = self.buf.readlines
                self.read = self.buf.read
                self.write = self.newbuf.write

            def __enter__(self, *args, **kwargs):
                return self

            def __exit__(self, *args, **kwargs):
                pass

        d = fake_fh(input_data)
        _open.return_value = d

        self.harness.charm._set_lvm_conf_global_filter(value)

        self.assertEqual(d.newbuf.getvalue(), updated_data)

        d = fake_fh(d.newbuf.getvalue())
        _open.return_value = d

        self.harness.charm._set_lvm_conf_global_filter(value + ' 2')

        self.assertEqual(d.newbuf.getvalue(), updated_data2)

        d = fake_fh(updated_data)
        _open.return_value = d
        self.harness.charm._set_lvm_conf_global_filter('')

        self.assertEqual(d.newbuf.getvalue(), input_data)

        d = fake_fh(input_data)
        _open.return_value = d
        self.harness.charm._set_lvm_conf_global_filter('')

        self.assertEqual(d.newbuf.getvalue(), input_data)

    def check_contents(self):
        pass

    def test_action_outfile_created(self):
        pass

    def test_action_no_autofix(self):
        pass

    def test_action_autofix(self):
        pass
