# Copyright 2014 Cisco Systems, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Nader Lahouti, Cisco Systems, Inc.

"""This module provides APIs for communicating with DCNM."""


try:
    import json
except ImportError:
    import simplejson as json
import requests

from dfa.common import dfa_exceptions as dexc
from dfa.common import dfa_logger as logging


LOG = logging.getLogger(__name__)


class DFARESTClient(object):

    """DFA client class that provides APIs to interact with DCNM."""

    def __init__(self, cfg):
        self._base_ver = '7.1(0)'
        self._ip = cfg.dcnm.dcnm_ip
        self._user = cfg.dcnm.dcnm_user
        self._pwd = cfg.dcnm.dcnm_password
        self._part_name = cfg.dcnm.default_partition_name
        if (not self._ip) or (not self._user) or (not self._pwd):
            msg = ("[DFARESTClient] Input DCNM IP, user name or password"
                   "parameter is not specified")
            raise ValueError(msg)

        self._req_headers = {'Accept': 'application/json',
                             'Content-Type': 'application/json; charset=UTF-8'}

        self.default_cfg_profile = cfg.dcnm.default_cfg_profile
        self.default_vrf_profile = cfg.dcnm.default_vrf_profile
        # url timeout: 10 seconds
        self.timeout_resp = 10

        # urls
        self._org_url = 'http://%s/rest/auto-config/organizations' % self._ip
        self._create_network_url = ('http://%s/' % self._ip +
                                    'rest/auto-config/organizations'
                                    '/%s/partitions/%s/networks')
        self._cfg_profile_list_url = ('http://%s/rest/auto-config/profiles' %
                                      self._ip)
        self._cfg_profile_get_url = self._cfg_profile_list_url + '/%s'
        self._global_settings_url = ('http://%s/rest/auto-config/settings' %
                                     self._ip)
        self._create_part_url = ('http://%s/rest/auto-config/' % self._ip +
                                 'organizations/%s/partitions')
        self._update_part_url = ('http://%s/rest/auto-config/' % self._ip +
                                 'organizations/%s/partitions/%s')
        self._del_org_url = ('http://%s/rest/auto-config/organizations' %
                             self._ip + '/%s')
        self._del_part = ('http://%s/rest/auto-config/organizations' %
                          self._ip + '/%s/partitions/%s')
        self._network_url = ('http://%s/rest/auto-config/organizations' %
                             self._ip + '/%s/partitions/%s/networks/'
                             'segment/%s')
        self._network_mob_url = ('http://%s/rest/auto-config/organizations' %
                                 self._ip + '/%s/partitions/%s/networks/'
                                 'vlan/%s/mobility-domain/%s')
        self._login_url = 'http://%s/rest/logon' % (self._ip)
        self._logout_url = 'http://%s/rest/logout' % (self._ip)
        self._exp_time = 100000
        self._resp_ok = (200, 201, 202)

        self._cur_ver = self.get_version()

        # Update the default network profile based on version of DCNM.
        self._set_default_cfg_profile()
        self._default_md = None

    @property
    def is_iplus(self):
        """Check the DCNM version."""

        return self._cur_ver >= self._base_ver

    def _set_default_cfg_profile(self):
        """Set default network config profile.

        Check whether the default_cfg_profile value exist in the current
        version of DCNM. If not, set it to new default value which is supported
        by latest version.
        """
        try:
            cfgplist = self.config_profile_list()
            if self.default_cfg_profile not in cfgplist:
                self.default_cfg_profile = ('defaultNetworkUniversalEfProfile'
                                            if self.is_iplus else
                                            'defaultNetworkIpv4EfProfile')
        except dexc.DfaClientRequestFailed:
            LOG.error("Failed to send requst to DCNM.")
            self.default_cfg_profile = 'defaultNetworkIpv4EfProfile'

    def _create_network(self, network_info):
        """Send create network request to DCNM.

        :param network_info: network parameters to be created on DCNM
        """
        url = self._create_network_url % (network_info['organizationName'],
                                          network_info['partitionName'])
        payload = network_info

        LOG.info(('url %(url)s payload %(payload)s'),
                 {'url': url, 'payload': payload})
        return self._send_request('POST', url, payload, 'network')

    def _config_profile_get(self, thisprofile):
        """Get information of a config profile from DCNM.

        :param thisprofile: network config profile in request
        """
        url = self._cfg_profile_get_url % (thisprofile)
        payload = {}

        res = self._send_request('GET', url, payload, 'config-profile')
        if res and res.status_code in self._resp_ok:
            return res.json()

    def _config_profile_list(self):
        """Get list of supported config profile from DCNM."""
        url = self._cfg_profile_list_url
        payload = {}

        res = self._send_request('GET', url, payload, 'config-profile')
        if res and res.status_code in self._resp_ok:
            return res.json()

    def _get_settings(self):
        """Get global mobility domain from DCNM."""
        url = self._global_settings_url
        payload = {}
        res = self._send_request('GET', url, payload, 'settings')
        if res and res.status_code in self._resp_ok:
            return res.json()

    def _set_default_mobility_domain(self):
        settings = self._get_settings()
        LOG.info("settings is %s" % settings)

        if ('globalMobilityDomain' in settings.keys()):
            global_md = settings.get('globalMobilityDomain')
            self._default_md = global_md.get('name')
            LOG.info("setting default md to be %s" % self._default_md)
        else:
            self._default_md = "md0"

    def _create_org(self, name, desc):
        """Create organization on the DCNM.

        :param name: Name of organization
        :param desc: Description of organization
        """
        url = self._org_url
        payload = {
            "organizationName": name,
            "description": name if len(desc) == 0 else desc,
            "orchestrationSource": "Openstack Controller"}

        return self._send_request('POST', url, payload, 'organization')

    def _create_or_update_partition(self, org_name, part_name, dci_id,
                                    desc, vrf_prof=None,
                                    service_node_ip=None, operation='POST'):
        """Send create or update partition request to the DCNM.

        :param org_name: name of organization
        :param part_name: name of partition
        :param desc: description of partition
        """
        if part_name is None:
            part_name = self._part_name
        if vrf_prof is None:
            vrf_prof = self.default_vrf_profile
        url = ((self._create_part_url % (org_name)) if operation == 'POST' else
               self._update_part_url % (org_name, part_name))

        payload = {
            "partitionName": part_name,
            "description": part_name if len(desc) == 0 else desc,
            "serviceNodeIpAddress": service_node_ip,
            "organizationName": org_name}

        # Check the DCNM version and find out whether it is need to have
        # extra payload for the new version when creating/updating a partition.
        if self.is_iplus:
            # Need to add extra payload for the new version.
            enable_dci = "true" if dci_id and int(dci_id) != 0 else "false"
            extra_payload = {
                "vrfProfileName": vrf_prof,
                "vrfName": ':'.join((org_name, part_name)),
                "dciId": dci_id,
                "enableDCIExtension": enable_dci}
            payload.update(extra_payload)

        return self._send_request(operation, url, payload, 'partition')

    def _get_partition(self, org_name, part_name=None):
        """send get partition request to the DCNM.
        :param org_name: name of organization
        :param part_name: name of partition
        """
        if part_name is None:
            part_name = self._part_name
        url = self._update_part_url % (org_name, part_name)
        res = self._send_request("GET", url, '', 'partition')
        if res and res.status_code in self._resp_ok:
            return res.json()

    def update_partition_static_route(self, org_name, part_name,
                                      static_ip_list, vrf_prof=None,
                                      service_node_ip=None):
        """
        Send static route update requests to DCNM.
        :param org_name: name of organization
        :param part_name: name of partition
        :static_ip_list: List of static IP addresses
        :vrf_prof: VRF Profile
        :service_node_ip: Service Node IP address
        """
        if part_name is None:
            part_name = self._part_name
        if vrf_prof is None:
            vrf_prof = self.default_vrf_profile
        operation = 'PUT'
        url = (self._update_part_url % (org_name, part_name))
        ip_str = ''
        ip_cnt = 0
        for ip in static_ip_list:
            ip_sub = "$n0" + str(ip_cnt) + "=" + str(ip) + ";"
            ip_str = ip_str + ip_sub
            ip_cnt = ip_cnt + 1
        cfg_args = {
            "$vrfName=" + org_name + ':' + part_name + ";"
            "$include_serviceNodeIpAddress=" + service_node_ip + ";"
            + ip_str
        }
        cfg_args = ';'.join(cfg_args)
        payload = {
            "partitionName": part_name,
            "organizationName": org_name,
            "dciExtensionStatus": "Not configured",
            "vrfProfileName": vrf_prof,
            "vrfName": ':'.join((org_name, part_name)),
            "configArg": cfg_args}

        return self._send_request(operation, url, payload, 'partition')

    def _delete_org(self, org_name):
        """Send organization delete request to DCNM.

        :param org_name: name of organization to be deleted
        """
        url = self._del_org_url % (org_name)
        return self._send_request('DELETE', url, '', 'organization')

    def _delete_partition(self, org_name, partition_name):
        """Send partition delete request to DCNM.

        :param partition_name: name of partition to be deleted
        """
        url = self._del_part % (org_name, partition_name)
        return self._send_request('DELETE', url, '', 'partition')

    def _delete_network(self, network_info):
        """Send network delete request to DCNM.

        :param network_info: contains network info to be deleted.
        """
        org_name = network_info.get('organizationName', '')
        part_name = network_info.get('partitionName', '')
        segment_id = network_info['segmentId']
        if 'mobDomainName' in network_info:
            vlan_id = network_info['vlanId']
            mob_dom_name = network_info['mobDomainName']
            url = self._network_mob_url % (org_name, part_name, vlan_id,
                                           mob_dom_name)
        else:
            url = self._network_url % (org_name, part_name, segment_id)
        return self._send_request('DELETE', url, '', 'network')

    def _get_network(self, network_info):
        """Send network get request to DCNM.

        :param network_info: contains network info to query.
        """
        org_name = network_info.get('organizationName', '')
        part_name = network_info.get('partitionName', '')
        segment_id = network_info['segmentId']
        url = self._network_url % (org_name, part_name, segment_id)
        return self._send_request('GET', url, '', 'network')

    def _login(self):
        """Login request to DCNM."""
        url_login = self._login_url
        expiration_time = self._exp_time

        payload = {'expirationTime': expiration_time}
        res = requests.post(url_login,
                            data=json.dumps(payload),
                            headers=self._req_headers,
                            auth=(self._user, self._pwd),
                            timeout=self.timeout_resp)
        session_id = ''
        if res and res.status_code in self._resp_ok:
            session_id = res.json().get('Dcnm-Token')
        self._req_headers.update({'Dcnm-Token': session_id})

    def _logout(self):
        """Logout request to DCNM."""
        url_logout = self._logout_url
        requests.post(url_logout,
                      headers=self._req_headers,
                      timeout=self.timeout_resp)

    def _send_request(self, operation, url, payload, desc):
        """Send request to DCNM."""

        res = None
        try:
            payload_json = None
            if payload and payload != '':
                payload_json = json.dumps(payload)
            self._login()
            desc_lookup = {'POST': ' creation', 'PUT': ' update',
                           'DELETE': ' deletion', 'GET': ' get'}

            res = requests.request(operation, url, data=payload_json,
                                   headers=self._req_headers,
                                   timeout=self.timeout_resp)
            desc += desc_lookup.get(operation, operation.lower())
            LOG.info(("DCNM-send_request: %(desc)s %(url)s %(pld)s"),
                     {'desc': desc, 'url': url, 'pld': payload})

            self._logout()
        except (requests.HTTPError, requests.Timeout,
                requests.ConnectionError) as exc:
            LOG.exception(('Error during request'))
            raise dexc.DfaClientRequestFailed(reason=exc)

        return res

    def config_profile_list(self):
        """Return config profile list from DCNM."""
        profile_list = []
        these_profiles = []
        these_profiles = self._config_profile_list()
        profile_list = [q for p in these_profiles for q in
                        [p.get('profileName')]]
        return profile_list

    def config_profile_fwding_mode_get(self, profile_name):
        """Return forwarding mode of given config profile."""
        profile_params = self._config_profile_get(profile_name)
        fwd_cli = 'fabric forwarding mode proxy-gateway'
        if profile_params and fwd_cli in profile_params['configCommands']:
            return 'proxy-gateway'
        else:
            return 'anycast-gateway'

    def get_config_profile_for_network(self, net_name):
        """Get the list of profiles."""

        cfgplist = self.config_profile_list()
        cfgname = net_name.partition(':')[2]

        cfgtuple = set()
        for cfg_prof in cfgplist:
            if cfg_prof.startswith('defaultNetwork'):
                cfg_alias = (cfg_prof.split('defaultNetwork')[1].
                             split('Profile')[0])
            elif cfg_prof.endswith('Profile'):
                cfg_alias = cfg_prof.split('Profile')[0]
            else:
                cfg_alias = cfg_prof
            cfgtuple.update([(cfg_prof, cfg_alias)])
        cfgp = [a for a, b in cfgtuple if cfgname == b]
        prof = cfgp[0] if cfgp else self.default_cfg_profile
        fwd_mod = self.config_profile_fwding_mode_get(prof)
        return (prof, fwd_mod)

    def create_network(self, tenant_name, network, subnet, part=None):
        """Create network on the DCNM.

        :param tenant_name: name of tenant the network belongs to
        :param network: network parameters
        :param subnet: subnet parameters of the network
        """
        network_info = {}
        seg_id = str(network.segmentation_id)
        subnet_ip_mask = subnet.cidr.split('/')
        gw_ip = subnet.gateway_ip
        if part is None:
            part = self._part_name
        cfg_args = [
            "$segmentId=" + seg_id,
            "$netMaskLength=" + subnet_ip_mask[1],
            "$gatewayIpAddress=" + gw_ip,
            "$networkName=" + network.name,
            "$vlanId=0",
            "$vrfName=" + tenant_name + ':' + part
        ]
        cfg_args = ';'.join(cfg_args)

        ip_range = ','.join(["%s-%s" % (p['start'], p['end']) for p in
                             subnet.allocation_pools])

        dhcp_scopes = {'ipRange': ip_range,
                       'subnet': subnet.cidr,
                       'gateway': gw_ip}

        network_info = {"segmentId": seg_id,
                        "vlanId": "0",
                        "mobilityDomainId": "None",
                        "profileName": network.config_profile,
                        "networkName": network.name,
                        "configArg": cfg_args,
                        "organizationName": tenant_name,
                        "partitionName": part,
                        "description": network.name,
                        "dhcpScope": dhcp_scopes}
        if self.is_iplus:
            # Need to add the vrf name to the network info
            prof = self._config_profile_get(network.config_profile)
            if prof and prof.get('profileSubType') == 'network:universal':
                # For universal profile vrf has to e organization:partition
                network_info["vrfName"] = ':'.join((tenant_name,
                                                    part))
            else:
                # Otherwise, it should be left empty.
                network_info["vrfName"] = ""

        LOG.debug("Creating %s network in DCNM.", network_info)

        res = self._create_network(network_info)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Created %s network in DCNM.", network_info)
        else:
            LOG.error("Failed to create %s network in DCNM.", network_info)
            raise dexc.DfaClientRequestFailed(reason=res)

    def create_service_network(self, tenant_name, network, subnet,
                               dhcp_range=True):
        """Create network on the DCNM.

        :param tenant_name: name of tenant the network belongs to
        :param network: network parameters
        :param subnet: subnet parameters of the network
        """
        network_info = {}
        subnet_ip_mask = subnet.cidr.split('/')
        if self._default_md is None:
            self._set_default_mobility_domain()
        vlan_id = '0'
        gw_ip = subnet.gateway_ip
        part_name = network.part_name
        if not part_name:
            part_name = self._part_name
        if network.mob_domain:
            mob_domain_name = network.mob_domain_name
        else:
            mob_domain_name = self._default_md

        if network.vlan_id:
            vlan_id = str(network.vlan_id)
        else:
            mob_domain_name = None

        seg_id = str(network.segmentation_id)
        seg_str = "$segmentId=" + seg_id
        cfg_args = [
            seg_str,
            "$netMaskLength=" + subnet_ip_mask[1],
            "$gatewayIpAddress=" + gw_ip,
            "$networkName=" + network.name,
            "$vlanId=" + vlan_id,
            "$vrfName=" + tenant_name + ':' + part_name
        ]
        cfg_args = ';'.join(cfg_args)

        ip_range = ','.join(["%s-%s" % (p['start'], p['end']) for p in
                             subnet.allocation_pools])

        dhcp_scopes = {'ipRange': ip_range,
                       'subnet': subnet.cidr,
                       'gateway': gw_ip}

        network_info = {"vlanId": vlan_id,
                        "mobilityDomainId": mob_domain_name,
                        "profileName": network.config_profile,
                        "networkName": network.name,
                        "configArg": cfg_args,
                        "organizationName": tenant_name,
                        "partitionName": part_name,
                        "description": network.name}
        if seg_id:
            network_info["segmentId"] = seg_id
        if dhcp_range:
            network_info["dhcpScope"] = dhcp_scopes
        if self.is_iplus:
            # Need to add the vrf name to the network info
            prof = self._config_profile_get(network.config_profile)
            if prof and prof.get('profileSubType') == 'network:universal':
                # For universal profile vrf has to e organization:partition
                network_info["vrfName"] = ':'.join((tenant_name, part_name))
            else:
                # Otherwise, it should be left empty.
                network_info["vrfName"] = ""

        LOG.debug("Creating %s network in DCNM.", network_info)

        res = self._create_network(network_info)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Created %s network in DCNM.", network_info)
        else:
            LOG.error("Failed to create %s network in DCNM.", network_info)
            raise dexc.DfaClientRequestFailed(reason=res)

    def delete_network(self, tenant_name, network):
        """Delete network on the DCNM.

        :param tenant_name: name of tenant the network belongs to
        :param network: object that contains network parameters
        """
        network_info = {}
        seg_id = network.segmentation_id
        network_info = {
            'organizationName': tenant_name,
            'partitionName': self._part_name,
            'segmentId': seg_id,
        }
        LOG.debug("Deleting %s network in DCNM.", network_info)

        res = self._delete_network(network_info)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Deleted %s network in DCNM.", network_info)
        else:
            LOG.error("Failed to delete %s network in DCNM.", network_info)
            raise dexc.DfaClientRequestFailed(reason=res)

    def delete_service_network(self, tenant_name, network):
	"""Delete network on the DCNM.

        :param tenant_name: name of tenant the network belongs to
        :param network: object that contains network parameters
        """

        network_info = {}
        part_name = network.part_name
        if not part_name:
            part_name = self._part_name
        if network.mob_domain_name:
            mob_domain_name = network.mob_domain_name
            vlan_id = str(network.vlan_id)
        else:
            vlan_id = '0'
            mob_domain_name = None
        seg_id = str(network.segmentation_id)
        network_info = {
            'organizationName': tenant_name,
            'partitionName': part_name,
            'mobDomainName': mob_domain_name,
            'vlanId': vlan_id,
            'segmentId': seg_id,
        }
        LOG.debug("Deleting %s network in DCNM.", network_info)

        res = self._delete_network(network_info)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Deleted %s network in DCNM.", network_info)
        else:
            LOG.error("Failed to delete %s network in DCNM.", network_info)
            raise dexc.DfaClientRequestFailed(reason=res)

    def delete_project(self, tenant_name, part_name):
        """Delete project on the DCNM.

        :param tenant_name: name of project to be deleted.
        """
        res = self._delete_partition(tenant_name, part_name)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Deleted %s partition in DCNM.", part_name)
        else:
            LOG.error("Failed to delete %(part)s partition in DCNM."
                      "Response: %(res)s", ({'part': part_name, 'res': res}))
            raise dexc.DfaClientRequestFailed(reason=res)

        res = self._delete_org(tenant_name)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Deleted %s organization in DCNM.", tenant_name)
        else:
            LOG.error("Failed to delete %(org)s organization in DCNM."
                      "Response: %(res)s", (
                          {'org': tenant_name,
                           'res': res}))
            raise dexc.DfaClientRequestFailed(reason=res)

    def delete_partition(self, org_name, partition_name):
        """Send partition delete request to DCNM.

        :param partition_name: name of partition to be deleted
        """
        res = self._delete_partition(org_name, partition_name)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Deleted %s partition in DCNM.", partition_name)
        else:
            LOG.error("Failed to delete %(part)s partition in DCNM."
                      "Response: %(res)s",
                      ({'part': partition_name, 'res': res}))
            raise dexc.DfaClientRequestFailed(reason=res)

    def create_project(self, org_name, part_name, dci_id, desc=None):
        """Create project on the DCNM.

        :param org_name: name of organization to be created
        :param desc: string that describes organization
        """
        desc = desc or org_name
        res = self._create_org(org_name, desc)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Created %s organization in DCNM.", org_name)
        else:
            LOG.error("Failed to create %(org)s organization in DCNM."
                      "Response: %(res)s", ({'org': org_name, 'res': res}))
            raise dexc.DfaClientRequestFailed(reason=res)

        self.create_partition(org_name, part_name, dci_id,
                              self.default_vrf_profile, desc=desc)

    def update_project(self, org_name, part_name, dci_id, service_node_ip=None,
                       vrf_prof=None, desc=None):
        """Update project on the DCNM.

        :param org_name: name of organization to be created
        :param desc: string that describes organization
        """
        desc = desc or org_name
        res = self._create_or_update_partition(org_name, part_name, dci_id,
                                               desc,
                                               service_node_ip=service_node_ip,
                                               vrf_prof=vrf_prof,
                                               operation='PUT')
        if res and res.status_code in self._resp_ok:
            LOG.debug("Update %s partition in DCNM.", part_name)
        else:
            LOG.error("Failed to update %(part)s partition in DCNM."
                      "Response: %(res)s", ({'part': part_name, 'res': res}))
            raise dexc.DfaClientRequestFailed(reason=res)

    def create_partition(self, org_name, part_name, dci_id, vrf_prof,
                         service_node_ip=None, desc=None):
        """Create partition on the DCNM.

        :param org_name: name of organization to be created
        :param part_name: name of partition to be created
        :param dci_id: DCI ID
        :vrf_prof: VRF profile for the partition
        :param desc: string that describes organization
        """
        desc = desc or org_name
        res = self._create_or_update_partition(org_name, part_name,
                                               dci_id, desc,
                                               service_node_ip=service_node_ip,
                                               vrf_prof=vrf_prof)
        if res and res.status_code in self._resp_ok:
            LOG.debug("Created %s partition in DCNM.", part_name)
        else:
            LOG.error("Failed to create %(part)s partition in DCNM."
                      "Response: %(res)s", ({'part': part_name, 'res': res}))
            raise dexc.DfaClientRequestFailed(reason=res)

    def get_partition_vrfProf(self, org_name, part_name=None):
        """get partition on the DCNM.

        :param org_name: name of organization
        :param part_name: name of partition
        """
        vrf_profile = None
        part_info = self._get_partition(org_name, part_name)
        LOG.info("query result from dcnm for partition info is %s", part_info)
        if ("vrfProfileName" in part_info):
            vrf_profile = part_info.get("vrfProfileName")
        return vrf_profile

    def list_networks(self, org, part):
        """Return list of networks from DCNM."""

        if org and part:
            list_url = self._del_part + '/networks'
            list_url = list_url % (org, part)
            res = self._send_request('GET', list_url, '', 'networks')
            if res and res.status_code in self._resp_ok:
                return res.json()

    def list_organizations(self):
        """Return list of organizations from DCNM."""

        try:
            res = self._send_request('GET', self._org_url, '', 'organizations')
            if res and res.status_code in self._resp_ok:
                return res.json()
        except dexc.DfaClientRequestFailed:
            LOG.error("Failed to send request to DCNM.")

    def get_network(self, org, segid):
        """Return given network from DCNM."""

        network_info = {}
        network_info = {
            'organizationName': org,
            'partitionName': self._part_name,
            'segmentId': segid,
        }
        res = self._get_network(network_info)
        if res and res.status_code in self._resp_ok:
            return res.json()

    def get_version(self):
        """Get the DCNM version."""

        url = 'http://%s/rest/dcnm-version' % self._ip
        payload = {}

        try:
            res = self._send_request('GET', url, payload, 'dcnm-version')
            if res and res.status_code in self._resp_ok:
                return res.json().get('Dcnm-Version')
        except dexc.DfaClientRequestFailed:
            LOG.error("Failed to get DCNM version.")
