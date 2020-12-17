#!/usr/bin/python
# -*- coding: utf-8 -*-

import jsonpickle

from cloudshell.api.cloudshell_api import CloudShellAPISession

from cloudshell.shell.core.resource_driver_interface import ResourceDriverInterface
from cloudshell.shell.core.driver_context import InitCommandContext, AutoLoadAttribute, AutoLoadDetails

from cloudshell.shell.core.driver_context import ApiVmDetails, ApiVmCustomParam

# from cloudshell.cp.vcenter.common.cloud_shell.driver_helper import CloudshellDriverHelper
from cloudshell.cp.vcenter.commands.load_vm import VMLoader
from cloudshell.cp.vcenter.common.vcenter.vmomi_service import pyVmomiService
from pyVim.connect import SmartConnect, Disconnect
from cloudshell.cp.vcenter.models.QualiDriverModels import AutoLoadAttribute, AutoLoadDetails
from cloudshell.cp.vcenter.common.model_factory import ResourceModelParser
from cloudshell.cp.vcenter.vm.ip_manager import VMIPManager
from cloudshell.devices.driver_helper import get_logger_with_thread_id
from cloudshell.cp.vcenter.common.vcenter.task_waiter import SynchronousTaskWaiter


VCENTER_CONNECTION_PORT = 443


class BreakingPointVChassisShellDriver(ResourceDriverInterface):
    DOMAIN = "Global"
    SHELL_NAME = "BP vChassis"

    def __init__(self):
        # self.cs_helper = CloudshellDriverHelper()
        self.model_parser = ResourceModelParser()
        self.ip_manager = VMIPManager()
        self.task_waiter = SynchronousTaskWaiter()
        self.pv_service = pyVmomiService(SmartConnect, Disconnect, self.task_waiter)

    def initialize(self, context):
        """
        Initialize the driver session, this function is called everytime a new instance of the driver is created
        This is a good place to load and cache the driver configuration, initiate sessions etc.
        :param InitCommandContext context: the context the command runs on
        """
        pass

    def cleanup(self):
        """
        Destroy the driver session, this function is called everytime a driver instance is destroyed
        This is a good place to close any open sessions, finish writing to log files
        """
        pass

    def get_inventory(self, context):
        """
        Will locate vm in vcenter and fill its uuid
        :type context: cloudshell.shell.core.context.ResourceCommandContext
        """

        logger = get_logger_with_thread_id(context)
        logger.info("Start Autoload process")

        vcenter_vm_name = context.resource.attributes["{}.vCenter VM".format(self.SHELL_NAME)]
        vcenter_vm_name = vcenter_vm_name.replace("\\", "/")
        vcenter_name = context.resource.attributes["{}.vCenter Name".format(self.SHELL_NAME)]

        logger.info("Start AutoLoading VM_path: {0} on vcenter: {1}".format(vcenter_vm_name, vcenter_name))

        # session = self.cs_helper.get_session(context.connectivity.server_address,
        #                                      context.connectivity.admin_auth_token,
        #                                      self.DOMAIN)

        session = CloudShellAPISession(host=context.connectivity.server_address,
                                       token_id=context.connectivity.admin_auth_token,
                                       domain=self.DOMAIN)

        vcenter_api_res = session.GetResourceDetails(vcenter_name)
        vcenter_resource = self.model_parser.convert_to_vcenter_model(vcenter_api_res)

        si = None

        try:
            logger.info("Connecting to vcenter ({0})".format(vcenter_api_res.Address))
            si = self._get_connection_to_vcenter(self.pv_service, session, vcenter_resource, vcenter_api_res.Address)

            logger.info("Loading VM UUID")
            vm_loader = VMLoader(self.pv_service)
            uuid = vm_loader.load_vm_uuid_by_name(si, vcenter_resource, vcenter_vm_name)
            logger.info("VM UUID: {0}".format(uuid))
            logger.info("Loading the IP of the VM")
            ip = self._try_get_ip(self.pv_service, si, uuid, vcenter_resource, logger)
            if ip:
                session.UpdateResourceAddress(context.resource.name, ip)

            autoload_atts = [AutoLoadAttribute("", "VmDetails", self._get_vm_details(uuid, vcenter_name))]
            return AutoLoadDetails([], autoload_atts)

        except Exception:
            logger.exception("Get inventory command failed")
            raise
        finally:
            if si:
                self.pv_service.disconnect(si)

    def _try_get_ip(self, pv_service, si, uuid, vcenter_resource, logger):
        ip = None
        try:
            vm = pv_service.get_vm_by_uuid(si, uuid)
            ip_res = self.ip_manager.get_ip(vm,
                                            vcenter_resource.holding_network,
                                            self.ip_manager.get_ip_match_function(None),
                                            cancellation_context=None,
                                            timeout=None,
                                            logger=logger)
            if ip_res.ip_address:
                ip = ip_res.ip_address
        except Exception:
            logger.debug("Error while trying to load VM({0}) IP".format(uuid), exc_info=True)
        return ip

    @staticmethod
    def _get_vm_details(uuid, vcenter_name):

        vm_details = ApiVmDetails()
        vm_details.UID = uuid
        vm_details.CloudProviderName = vcenter_name
        vm_details.CloudProviderFullName = vcenter_name
        vm_details.VmCustomParams = []
        str_vm_details = jsonpickle.encode(vm_details, unpicklable=False)
        return str_vm_details

    def _get_connection_to_vcenter(self, pv_service, session, vcenter_resource, address):
        password = self._decrypt_password(session, vcenter_resource.password)
        si = pv_service.connect(address, vcenter_resource.user, password, VCENTER_CONNECTION_PORT)
        return si

    @staticmethod
    def _decrypt_password(session, password):
        return session.DecryptPassword(password).Value
