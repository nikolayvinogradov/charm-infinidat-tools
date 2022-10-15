Infinidat Tools charm
---------------------

Overview
========

This charm provides configuration and tools for principal charms, such as cinder and nova-compute charms.

To use as a nova-compute subordinate:

    juju deploy nova
    juju deploy infinidat-tools
    juju add-relation infinidat-tools:storage-backend nova:storage-backend

Configuration
=============

See config.yaml for details of configuration options.
