"""Linux child lifetime guard, including abrupt death of its supervising process."""
import ctypes
import os
import sys


def die_with_parent(parent_pid: int, death_signal: int = 9):
    if sys.platform != 'linux':
        raise RuntimeError('Worker child supervision requires the Linux application image.')
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,death_signal,0,0,0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(),'Unable to set parent-death signal.')
    # Close the fork-to-prctl race if the parent already exited.
    if os.getppid()!=parent_pid:
        os.kill(os.getpid(),death_signal)
