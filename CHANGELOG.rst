=============================
Ngine\_Io.Hashi Release Notes
=============================

.. contents:: Topics

v1.9.0
======

Minor Changes
-------------

- nomad, consul - ``nomad__extra_config``, ``consul__extra_config``, ``consul__services``, ``consul__checks`` and ``consul__watches`` set to ``null`` (e.g. left empty in inventory) are treated as empty.
- nomad, consul, common, cni, nomad_upgrade - read facts through the ``ansible_facts`` namespace instead of injected ``ansible_*`` variables, and use explicit boolean conditionals as required by ansible-core 2.19+.

v1.8.0
======

New Modules
-----------

- ngine_io.hashi.nomad_agent_info - Gather information about a Nomad agent.
- ngine_io.hashi.nomad_job - Submit and remove Nomad jobs.
- ngine_io.hashi.nomad_node - Manage scheduling eligibility and drain of a Nomad client node.
- ngine_io.hashi.nomad_raft_info - Gather information about the Nomad Raft peer set.
