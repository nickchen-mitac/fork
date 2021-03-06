# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

"""
Loading modules that provide task codes.
"""
import os
import time
import ast
import glob
import logging
import gevent
import datetime
import collections
import calendar
import heapq
import bisect
import array
import Queue

import string
import re

import math
import random

import json
import lxml

from gevent import Greenlet

from avame.schedule import Schedule
from .validator import ScriptValidator
from ava.util import time_uuid
from ava.runtime import environ
from ava.task.service import get_task_engine
from . import signals
from .defines import ENGINE_NAME
from .errors import JobCancelledError

logger = logging.getLogger(__name__)

_AVATARS_DIR = 'jobs'


class JobInfo(object):
    """ Metadata of a task definition
    """

    def __init__(self, job_id, name, script, acode):
        self._id = job_id
        self._name = name
        self._script = script
        self._code = acode
        self._started_at = datetime.datetime.now()

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        """ Unique name for the task.
        :return: task's name
        """
        return self._name

    @property
    def script(self):
        return self._script

    @property
    def code(self):
        return self._code

    @property
    def started_time(self):
        return self._started_at

    @property
    def started_time_iso(self):
        return self._started_at.isoformat()


class JobContext(object):
    """ The context of a task object.
    """

    _logger = logging.getLogger('ava.job')

    def __init__(self, job_info, core_context, parent=None, args=[], kwargs={}):
        self._job_info = job_info
        self._scope = {}
        self._core = core_context
        self._scope['ava'] = self
        self._scope['parent'] = parent
        self._scope['args'] = args
        self._scope['kwargs'] = kwargs
        self._populate_scope()
        self._task_engine = get_task_engine()
        self.exception = None
        self.result = None

    @property
    def name(self):
        return self._job_info.name

    @property
    def job_id(self):
        return self._job_info.id

    @property
    def log(self):
        return self._logger

    def _populate_scope(self):
        self._scope['datetime'] = datetime
        self._scope['collections'] = collections
        self._scope['calendar'] = calendar
        self._scope['heapq'] = heapq
        self._scope['bisect'] = bisect
        self._scope['array'] = array
        self._scope['queue'] = Queue
        self._scope['Queue'] = Queue  # to be compatible with PY2
        self._scope['string'] = string
        self._scope['re'] = re
        self._scope['math'] = math
        self._scope['random'] = random
        self._scope['json'] = json
        self._scope['lxml'] = lxml

    def sleep(self, secs):
        time.sleep(secs)

    def localtime(self):
        """
        Gets the local time in seconds since epoch.
        """
        time.localtime()

    def gmtime(self):
        return time.gmtime()

    def do(self, action, *args, **kwargs):
        return self._task_engine.do_task(self._job_info.id, action, *args, **kwargs)

    def wait(self, tasks, timeout=None, count=None):
        """
        Wait for tasks to finished with optional timeout.
        """

        assert isinstance(tasks, list)
        assert len(tasks) > 0

        gevent.wait(tasks, timeout, count)

    @property
    def schedule(self):
        return Schedule()


class JobRunner(Greenlet):
    def __init__(self, engine, job_info, job_context):
        Greenlet.__init__(self)
        self.engine = engine
        self.job_ctx = job_context
        self.job_info = job_info

    def _run(self):
        logger.info("Running job: %s", self.job_ctx.name)

        try:
            global_scope = dict()
            global_scope['__builtin__'] = {}

            exec self.job_info.code in global_scope, self.job_ctx._scope
            if 'result' in self.job_ctx._scope:
                self.job_ctx.result = self.job_ctx._scope.get('result')
        except Exception as ex:
            logger.error("Error in running job: %s", self.job_ctx.name, exc_info=True)
            self.job_ctx.exception = ex
        finally:
            self.engine.job_done(self.job_ctx)


class JobEngine(object):
    """
    Responsible for managing application modules.
    """

    def __init__(self):
        self.jobs = {}
        self.contexts = {}
        self.runners = {}

        self.jobs_path = os.path.join(environ.pod_dir(), _AVATARS_DIR)
        self.jobs_path = os.path.abspath(self.jobs_path)
        self.validator = ScriptValidator()
        self._core_context = None
        self._stopping = False
        self._task_engine = False

    def _scan_jobs(self):
        pattern = os.path.join(self.jobs_path, '[a-zA-Z][a-zA-Z0-9_]*.py')
        return glob.glob(pattern)

    def _load_jobs(self, ctx):
        logger.debug("Job directory: %s", self.jobs_path)

        job_files = self._scan_jobs()

        logger.debug("Found %d job(s)" % len(job_files))

        for s in job_files:
            name = os.path.basename(s)
            if '__init__.py' == name:
                continue

            # gets the basename without extension part.
            name = os.path.splitext(name)[0]
            try:
                logger.debug("Loading job: %s", name)
                with open(s, 'r') as f:
                    script = f.read()

                node = ast.parse(script, filename=name, mode='exec')
                self.validator.visit(node)
                acode = compile(node, filename=name, mode='exec')
                jid = self._gen_job_id()
                job_info = JobInfo(jid, name, script, acode)
                self.jobs[jid] = job_info
                self.contexts[jid] = JobContext(job_info, self._core_context)
            except Exception:
                logger.error("Failed to load job: %s", name, exc_info=True)

    def _run_jobs(self):

        logger.debug("Starting jobs...")
        for job_id in self.jobs:
            info = self.jobs[job_id]
            ctx = self.contexts[job_id]
            runner = JobRunner(self, info, ctx)
            self.runners[job_id] = runner
            runner.start()

        while not self._stopping:
            time.sleep(1)

        logger.info("All jobs stopped.")

    def _gen_job_id(self):
        return time_uuid.oid()

    def validate_script(self, script):
        self.validator.validate(script)

    def submit_job(self, job):
        job_id = self._gen_job_id()

        try:
            script = job['script']
            job_name = job.get('name')
            if job_name is None:
                job_name = job_id
            node = ast.parse(script, filename=job_name, mode='exec')
            self.validator.visit(node)
            acode = compile(node, filename=job_name, mode='exec')
            job_info = JobInfo(job_id, job_name, script, acode)
            self.jobs[job_id] = job_info
            ctx = JobContext(job_info, self._core_context)
            self.contexts[job_id] = ctx
            runner = JobRunner(self, job_info, ctx)
            self.runners[job_id] = runner
            runner.start()
            self._core_context.send(signals.JOB_ACCEPTED, job_name=job_id)
            return job_id
        except (Exception, SyntaxError) as ex:
            logger.error("Failed to run job: %s", job_id, exc_info=True)
            if ex.message is not None and len(ex.message) > 0:
                reason = ex.message
            else:
                reason = str(ex)

            print("REASON:", reason)
            self._core_context.send(signals.JOB_REJECTED, reason=reason)

    def cancel_job(self, job_id):
        if job_id not in self.jobs:
            return

        ctx = self.contexts[job_id]
        runner = self.runners[job_id]

        try:
            runner.kill()
        except:
            pass

        ctx.exception = JobCancelledError()

        self.job_done(ctx)

    def job_done(self, job_context):
        """ Invoked by task runner to notify that a task is finished or failed.

        :param job_context:
        :return:
        """

        logger.debug("Job done: '%s'", job_context.job_id)

        job_id = job_context.job_id
        if job_id in self.jobs:
            del self.jobs[job_id]

        if job_id in self.runners:
            del self.runners[job_id]

        if job_id in self.contexts:
            del self.contexts[job_id]

        try:
            if job_context.exception is not None:
                self._core_context.send(signals.JOB_FAILED, job_ctx=job_context)
            else:
                self._core_context.send(signals.JOB_FINISHED, job_ctx=job_context)
        finally:
            self._task_engine.release_for_job(job_id)

    def start(self, ctx):
        logger.debug("Starting job engine...")
        self._task_engine = ctx.lookup('taskengine')
        ctx.bind(ENGINE_NAME, self)
        self._core_context = ctx
        self._load_jobs(ctx)
        ctx.add_child_greenlet(gevent.spawn(self._run_jobs))
        logger.debug("Job engine started.")

    def stop(self, ctx):
        self._stopping = True
        logger.debug("Job engine stopped.")
