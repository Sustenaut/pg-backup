"""Steps for the drill, and for waiting until one can run."""

import sys
import time
from datetime import timedelta

from pytest_bdd import given, parsers, scenarios, when

from pg_backup.config import load
from pg_backup.drill import drill, wait_until_ready

scenarios("drill.feature")


@given(parsers.parse("the drill's restore takes {seconds:d} seconds"))
def restore_takes(ctx, seconds):
    ctx.drill_clock = iter([0, seconds]).__next__


def _drill(ctx, to):
    time.tzset()
    clock = getattr(ctx, "drill_clock", iter([0, 15]).__next__)
    sys.exit(drill(load(), ctx.server, to, now=ctx.now, clock=clock))


@when("the drill runs")
def drill_runs(ctx):
    ctx.attempt(lambda: _drill(ctx, None))


@when(parsers.parse('the drill runs to "{moment}"'))
def drill_runs_to(ctx, moment):
    ctx.attempt(lambda: _drill(ctx, moment))


class Clock:
    """now() and sleep() over one fake time, from the scenario's moment; each sleep says so."""

    def __init__(self, start):
        self.at = start

    def now(self):
        return self.at

    def sleep(self, seconds):
        print(f"(slept {seconds} s)")
        self.at += timedelta(seconds=seconds)


@given("a clock that moves only when slept")
def clock_moves_when_slept(ctx):
    ctx.wait_clock = Clock(ctx.now())


@when("the drill waits until it can run")
def waits(ctx):
    time.tzset()
    ctx.attempt(lambda: wait_until_ready(load(), ctx.server, sleep=ctx.wait_clock.sleep, now=ctx.wait_clock.now))
