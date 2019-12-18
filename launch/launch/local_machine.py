# Copyright 2019 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import traceback
from typing import Dict

from launch.conditions import evaluate_condition_expression
from launch.events.process import ProcessStarted, ProcessExited, ProcessStdout, ProcessStderr
from launch.utilities import normalize_to_list_of_substitutions

import launch

from osrf_pycommon.process_utils import async_execute_process, AsyncSubprocessProtocol

from .launch_context import LaunchContext
from .machine import Machine


class LocalMachine(Machine):
    """Describes a machine for remotely launching ROS nodes."""

    def __init__(self, **kwargs) -> None:
        """Initialize a machine description."""
        super().__init__(**kwargs)

    class __ProcessProtocol(AsyncSubprocessProtocol):
        def __init__(
            self,
            action: 'ExecuteProcess',
            context: LaunchContext,
            process_event_args: Dict,
            **kwargs
        ) -> None:
            super().__init__(**kwargs)
            self.__context = context
            self.__action = action
            self.__process_event_args = process_event_args
            self.__logger = launch.logging.get_logger(process_event_args['name'])

        def connection_made(self, transport):
            self.__logger.info(
                'process started with pid [{}]'.format(transport.get_pid()),
            )
            super().connection_made(transport)
            self.__process_event_args['pid'] = transport.get_pid()
            self.__action._subprocess_transport = transport

        def on_stdout_received(self, data: bytes) -> None:
            self.__context.emit_event_sync(ProcessStdout(text=data, **self.__process_event_args))

        def on_stderr_received(self, data: bytes) -> None:
            self.__context.emit_event_sync(ProcessStderr(text=data, **self.__process_event_args))

    async def execute_process(self,
                              process_event_args: None,
                              log_cmd: False,
                              emulate_tty: False,
                              shell: False,
                              cleanup_fn: lambda: False,
                              context: LaunchContext) -> None:
        if process_event_args is None:
            raise RuntimeError('process_event_args unexpectedly None')
        cmd = process_event_args['cmd']
        cwd = process_event_args['cwd']
        env = process_event_args['env']
        logger = launch.logging.get_logger(process_event_args['name'])
        logger.info("LocalMachine.execute_process()")

        if log_cmd:
            logger.info("process details: cmd=[{}], cwd='{}', custom_env?={}".format(
                ', '.join(cmd), cwd, 'True' if env is not None else 'False'
            ))

        if 'emulate_tty' in context.launch_configurations:
            emulate_tty = evaluate_condition_expression(
                context,
                normalize_to_list_of_substitutions(
                    context.launch_configurations['emulate_tty']
                ),
            )

        try:
            logger.info("Executing process")
            transport, self._subprocess_protocol = await async_execute_process(
                lambda **kwargs: self.__ProcessProtocol(
                    self, context, process_event_args, **kwargs
                ),
                cmd=cmd,
                cwd=cwd,
                env=env,
                shell=shell,
                emulate_tty=emulate_tty,
                stderr_to_stdout=False,
            )
            logger.info("Executed")
        except Exception:
            logger.error('exception occurred while executing process:\n{}'.format(
                traceback.format_exc()
            ))
            cleanup_fn()
            return

        pid = transport.get_pid()

        logger.info("Emitting ProcessStarted event")
        await context.emit_event(ProcessStarted(**process_event_args))

        returncode = await self._subprocess_protocol.complete
        if returncode == 0:
            logger.info('process has finished cleanly [pid {}]'.format(pid))
        else:
            logger.error("process has died [pid {}, exit code {}, cmd '{}'].".format(
                pid, returncode, ' '.join(cmd)
            ))
        await context.emit_event(ProcessExited(returncode=returncode, **process_event_args))
        logger.info("Process exited")
        cleanup_fn()
