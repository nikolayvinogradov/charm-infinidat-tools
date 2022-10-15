#! /usr/bin/env python3

# Copyright 2022 Canonical Ltd
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

import os
import logging
import subprocess
import re
import tempfile
from pathlib import Path

from ops_openstack.core import OSBaseCharm
from ops.main import main

from ops.model import (
    ActiveStatus,
    BlockedStatus,
)

from charmhelpers.core.host import (
    lsb_release,
    service_restart,
)

from charmhelpers.fetch import (
    apt_install,
    apt_update,
    add_source,
)

from charmhelpers.fetch.archiveurl import ArchiveUrlFetchHandler

logger = logging.getLogger(__name__)

INFINIHOST_RESULTS_DIR = '/home/ubuntu/infinihost-results'
MULTIPATH_CONF = '/etc/multipath.conf'
DEFAULT_REPO_KEY_URL = 'https://repo.infinidat.com/packages/gpg.key'


class InfinidatToolsCharm(OSBaseCharm):

    # multipath-tools package should be installed by nova-compute
    PACKAGES = [
        'host-power-tools',
        'scsitools',
        'multipath-tools-boot'
    ]

    MANDATORY_CONFIG = ['install_sources']
    # Overriden from the parent. May be set depending on the charm's properties

    RESTART_MAP = {
        MULTIPATH_CONF: ['multipathd']
    }

    DEFAULT_REPO_BASEURL = \
        'https://repo.infinidat.com/packages/main-stable/apt/linux-ubuntu'

    stateless = True
    active_active = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.framework.observe(self.on.start, self.on_start)

        self.framework.observe(self.on.run_infinidat_settings_check_action,
                               self.on_run_infinidat_settings_check_action)

    def _run_infinihost_check(self, auto_fix=True, stdout=subprocess.PIPE):
        """
        host-power-tool utility checks environment for
        the recommended settings and if anything is missing
        (and --auto-fix is provided) then it:
        - generates default multipath.conf with recommended defaults
        - excludes local disks from multipath configuration
        - registers itself as a udev handler
        - regenerates initramfs, so that multipath configuration
        is applied to it as well, in case root fs is on SAN
        """
        p = None

        cmd = [
            'infinihost',
            'settings',
            'check'
        ]

        if auto_fix:
            cmd.append('--auto-fix')

        logging.debug("Executing: {0}".format(' '.join(cmd)))

        try:
            # FIXME: Without this there is a dependency conflict:
            # pkg_resources in charm's venv pythonpath takes precedence over
            # the system-wide one, and because of the version difference,
            # it breaks infinihost with "ImportError: cannot import name 'six'"
            env = os.environ.copy()
            if 'PYTHONPATH' in env:
                env.pop('PYTHONPATH')

            p = subprocess.Popen(cmd, shell=False, stdout=stdout, env=env)
        except FileNotFoundError:
            logging.fatal(
                "Failed to run 'infinihost': is host-power-tools installed?")
            raise

        if stdout == subprocess.PIPE:
            stdout, _ = p.communicate()
            logger.debug("infinihost output: {0}".format(stdout))

        code = p.wait()

        logger.info('infinihost exit code: {0}'
                    .format(code))

        return code

    def _update_multipath_conf(self, restart=True):

        replacements = (
            # Ensure that multipathd config file has skip_kpartx set to yes
            # (causes issues with volumes detaching if set to no)
            (re.compile(
                r'(skip_kpartx[ \t]+).*$', re.MULTILINE), 'yes'),
            (re.compile(
                r'(user_friendly_names[ \t]+).*$', re.MULTILINE), 'no'),
        )

        with open(MULTIPATH_CONF, 'r') as f:
            data = f.read()
            for pat, new_value in replacements:
                data = re.sub(
                    pat,
                    lambda m: '{0}"{1}"'.format(str(m.group(1)), new_value),
                    data
                )

        logger.info("Updating multipath.conf")
        with open(MULTIPATH_CONF, 'w') as f:
            f.write(data)

        logger.info("Restarting multipathd")
        if restart:
            service_restart('multipathd')

    def _set_lvm_conf_global_filter(self, lvm_global_filter,
                                    lvm_conf='/etc/lvm/lvm.conf'):
        # Ensure we have a lvm.conf filter in place to stop lvm groups
        # present on instance/VM being discovered by the host

        logging.info('Setting lvm.conf global_filter')

        with open(lvm_conf, 'r') as file:
            d = file.read()

        p = re.compile(r'(# global_filter = \[[^\n]*\n)')

        p_split = p.split(d, maxsplit=1)
        if len(p_split) == 3:
            the_rest = p_split.pop(2)

            the_next_line, nl, the_rest = the_rest.partition('\n')
            tmp = []
            if lvm_global_filter:
                tmp.append('global_filter = {0} # __infinidat_tools__'
                           .format(lvm_global_filter))

            if '__infinidat_tools__' not in the_next_line:
                tmp.append(the_next_line + nl + the_rest)
            else:
                tmp.append(the_rest)

            p_split.append('\n'.join(tmp))

            with open(lvm_conf, 'w') as file:
                file.write(''.join(p_split))
        else:
            logging.fatal(
                'Error while modifying lvm.conf: '
                'expected pattern not found'
            )
            self.unit.status = BlockedStatus('Failed to update lvm.conf')
            return

    def on_start(self, event):
        self._stored.is_started = True

    def _regenerate_initrd(self):
        # Regenerate initrd
        try:
            logging.info('Regenerating initrd')
            subprocess.check_call("update-initramfs -u -k all", shell=True)
        except subprocess.CalledProcessError as e:
            logging.fatal('Error regenerating initrd: {0}'.format(e))
            self.unit.status = BlockedStatus('error regenerating initrd')
            return

    def on_install(self, event):
        logging.info('Preparing Infinidat tools package installation')

        # initial installation, this is not called after reboot
        try:
            self.install_pkgs()
            code = self._run_infinihost_check(auto_fix=True)
            self._update_multipath_conf()
            self._regenerate_initrd()
        except Exception as e:
            logger.fatal("Failed to install packages: {0}".format(str(e)))
            # something failed, attempt rerunning the hook later
            self.unit.status = BlockedStatus("Installation failed")
            event.defer()
            return

        self._set_lvm_conf_global_filter(
            self.config.get('lvm_global_filter'))

        if code != 0:
            self.unit.status = ActiveStatus(
                "review infinihost settings status")
        else:
            self.unit.status = ActiveStatus()

    def _get_default_repo_key(self):
        url_fetcher = ArchiveUrlFetchHandler()
        with tempfile.NamedTemporaryFile() as outfile:
            outfile.close()
            dest_path = os.path.join(tempfile.gettempdir(), outfile.name)

            url_fetcher.download(DEFAULT_REPO_KEY_URL,
                                 dest_path)
            with open(dest_path) as f:
                return f.read()

    def install_pkgs(self):
        install_keys = self.model.config.get('install_keys')
        if not install_keys:
            install_keys = self._get_default_repo_key()

        # we implement $codename expansion here
        # see the default value for 'source' in config.yaml
        if self.model.config.get('install_sources'):
            distrib_codename = lsb_release()['DISTRIB_CODENAME'].lower()
            add_source(
                self.model.config['install_sources']
                    .format(distrib_codename=distrib_codename),
                self.model.config.get('install_keys'))
        apt_update(fatal=True)
        apt_install(self.PACKAGES, fatal=True)

    def on_config(self, event):
        self._set_lvm_conf_global_filter(
            self.config.get('lvm_global_filter'))

        self.unit.status = ActiveStatus()

    def on_run_infinidat_settings_check_action(self, event):
        Path(INFINIHOST_RESULTS_DIR).mkdir(parents=True, exist_ok=True)

        if event.params.get('auto-fix'):
            auto_fix = True
        else:
            auto_fix = False

        with tempfile.NamedTemporaryFile(mode="w+b", prefix='infinihost-out-',
                                         dir=INFINIHOST_RESULTS_DIR,
                                         delete=False) as outfile:
            try:
                event.log("Running 'infinihost settings check'")
                code = self._run_infinihost_check(auto_fix=auto_fix,
                                                  stdout=outfile)
                Path(outfile.name).chmod(0o644)
            except subprocess.CalledProcessError as e:
                msg = "Failed to run infinihost: {0}".format(str(e))
                logger.fatal(msg)
                event.fail(msg)

        if auto_fix:
            event.log("--auto-fix is enabled, updating multipath.conf")
            self._update_multipath_conf()

        event.set_results({
            "result":
                "exit code={0}\n"
                "see 'juju ssh -m {1} {2} cat {3}' for more details"
                .format(code, self.unit.name, self.model.name,
                        os.path.join(INFINIHOST_RESULTS_DIR, outfile.name))})


if __name__ == '__main__':
    main(InfinidatToolsCharm)
