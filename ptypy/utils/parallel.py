# -*- coding: utf-8 -*-
"""\
Utility functions and classes to support MPI computing.

This file is part of the PTYPY package.

    :copyright: Copyright 2014 by the PTYPY team, see AUTHORS.
    :license: GPLv2, see LICENSE for details.
"""
__all__ = ['MPIenabled', 'psize', 'prank', 'comm', 'MPI', 'master', 'LoadManager', 'loadmanager','MPIrand_normal','MPIrand_uniform']

import numpy as np

size = 1
rank = 0
MPI = None
comm = None

try:
    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    size = comm.Get_size()
    rank = comm.Get_rank()
except:
    print 'MPI initialization failed. Proceeding with one processor'

MPIenabled = not (size==1)
master = (rank==0)

def useMPI(do=None):
    """\
    Toggle using MPI or not. Is this useful?
    """
    global MPIenabled
    if do is None:
        return MPIenabled
    if MPI is None:
        MPIenabled = False
    else:
        MPIenabled = do


###################################
# Helper functions, wrapper to mpi4py
###################################

class LoadManager(object):
    
    def __init__(self):
        """
        LoadManager: keep track of the amount of data managed by each
        process and help keeping it balanced.
        """
        
        self.load = np.zeros((size,), dtype=int)
        self.rank_of = {}
    
    def assign(self, idlist=None):
        """
        
        Subdivide the provided list of ids into contiguous blocks to balance
        the load among the processes. 
        
        The elements of idlist are used as keys to subsequently identify
        the rank of a given id, through the dict self.rank_of. No check is done
        whether ids passed are unique or even hashable.
        
        If idlist is None, increase by one the load of the least busy process.
        self.rank_of is not updated in this case.
        
        return R, a list of list such that
        R[rank] = list of indices of idlist managed by process of given rank. 
        """
        
        # Simplest case
        if idlist is None:
            r = size - 1 - self.load[::-1].argmin()
            self.load[r] +=1
            return [[0] if rr==r else [] for rr in range(size)]
        
        # Total load
        Nid = len(idlist)
        total_load = (self.load.sum() + Nid)

        # Eliminate nodes that are too busy already
        li = total_load > self.load*size

        # Recompute total load among available nodes
        nsize = li.sum()
        total_load = (self.load[li].sum() + Nid)

        # Numerator part of the number of elements to assign to each node
        partition = total_load - self.load[li]*nsize

        # Integer part
        ipart = partition // nsize
        
        # Spread the fractional remainder among the nodes, starting from the
        # last one.
        rem = (partition % nsize).sum() // nsize
        ipart[:-int(1+rem):-1] += 1

        # Update the loads
        part = np.zeros_like(self.load)
        part[li] = ipart
        self.load += part

        # Cumulative sum give the index boundaries between the ranks 
        cumpart = np.cumsum(part)
        
        # Now assign the rank
        rlist = np.arange(size)
        out = [[] for x in range(size)]
        for i,k in enumerate(idlist):
            r = rlist[i < cumpart][0]
            out[r].append(i)
            self.rank_of[k] = r
        return out

# Create one instance - typically only this one should be used
loadmanager = LoadManager()

def allreduce(a, op=None):
    """
    Wrapper for comm.Allreduce, always in place.
    
    Parameters
    ----------
    a : numpy array
        The array to operate on.
    op : None or one of MPI.BAND, MPI.BOR, MPI.BXOR, MPI.LAND, MPI.LOR, 
         MPI.LXOR, MPI.MAX, MPI.MAXLOC, MPI.MIN, MPI.MINLOC, MPI.OP_NULL,
         MPI.PROD, MPI.REPLACE or MPI.SUM. 
         If None, use MPI.SUM.
    """

    if not MPIenabled: return
    if op is None:
        #print a.shape
        comm.Allreduce(MPI.IN_PLACE, a)
    else:
        comm.Allreduce(MPI.IN_PLACE, a, op=op)
    return

def _MPIop(a, op, axis=None):
    """
    Apply operation op on accross a list of arrays distributed between
    processes. Supported operations are SUM, MAX, MIN, and PROD. 
    """
    
    MPIop, npop = {'SUM':(MPI.SUM, np.sum), 'MAX':(MPI.MAX, np.max), 'MIN':(MPI.MIN, np.min), 'PROD':(MPI.PROD, np.prod)}[op.upper()]

    # Total op
    if axis is None:
        # Apply op on locally owned data (and wrap the scalar result in a numpy array
        s = np.array([ npop( [npop(ai) for ai in a if ai is not None] ) ])

        # Reduce and return scalar
        if MPIenabled:
            comm.Allreduce(MPI.IN_PLACE, s, op=MPIop)
        return s[0]

    # Axis across the processes
    elif axis==0:
        # Apply op on locally owned arrays
        s = npop(ai for ai in a if ai is not None)

        # Reduce and return result
        if MPIenabled:
            comm.Allreduce(MPI.IN_PLACE, s, op=MPIop)
        return s

    else:
        # No cross-talk needed
        return [npop(ai, axis=axis-1) if ai is not None else None for ai in a]

def MPIsum(a, axis=None):
    """
    Compute the sum of list of arrays distributed over multiple processes.
    """
    return _MPIop(a, op='SUM', axis=axis)

def MPImin(a, axis=None):
    """
    Compute the minimum over a list of arrays distributed over multiple processes.
    """
    return _MPIop(a, op='MIN', axis=axis)
    
def MPImax(a, axis=None):
    """
    Compute the maximum over a list of arrays distributed over multiple processes.
    """
    return _MPIop(a, op='MAX', axis=axis)

def MPIprod(a, axis=None):
    """
    Compute the product over a list of arrays distributed over multiple processes.
    """
    return _MPIop(a, op='PROD', axis=axis)

def barrier():
    """
    Wrapper for comm.Barrier.
    """

    if not MPIenabled: return
    comm.Barrier()
    
def send(data, dest=0, tag=0):
    """
    Wrapper for comm.Send
    
    Parameters
    ----------
    data : numpy array
           The array so send
    dest : int
           The rank of the destination process. Defaults to 0 (master).
    tag : int
          Defaults to 0.
    """

    # Send array info
    header = (data.shape, data.dtype.str)
    comm.send(header, dest=dest, tag=1)

    # Send data
    # mpi4py has in issue sending booleans. we convert to uint8 (same size)
    if data.dtype.str=='|b1': 
        comm.Send(data.astype('uint8'), dest=dest, tag=tag)
    else:
        comm.Send(data, dest=dest, tag=tag)
        
def receive(source=None, tag=0, out=None):
    """
    Wrapper for comm.Recv
    
    Parameters
    ----------
    source : int or None
             The rank of the process sending data. If None, this is set
             to MPI.ANY_SOURCE
    tag : int
          Not really useful here - default to 0 all the time
    out : numpy array or None
          If a numpy array, the transfered data will be stored in out. If
          None, a new array is created.
          
    Returns
    -------
    out : numpy array
    """

    if source is None: source = MPI.ANY_SOURCE

    # Receive array info
    shape, dtypestr = comm.recv(source=source, tag=1)
    
    newdtype = '|u1' if dtypestr=='|b1' else dtypestr
    # Create array if none is provided
    if out is None:
        out = np.empty(shape, dtype=newdtype)

    # Receive raw data
    comm.Recv(out, source=source, tag=tag)

    if dtypestr=='|b1':
        return out.astype('bool')
    else:
        return out

def MPIrand_normal(loc=0.0,scale=1.0,size=(1)):
    """
    wrapper for np.random.normal for same random sample across all nodes.
    """
    if master:
        sample = np.array(np.random.normal(loc=loc,scale=scale,size=size))
    else:
        sample = np.zeros(size)
    allreduce(sample)
    return sample

def MPIrand_uniform(low=0.0,high=1.0,size=(1)):
    """
     wrapper for np.random.uniform for same random sample across all nodes.
    """
    if master:
        sample = np.array(np.random.uniform(low=low,high=high,size=size))
    else:
        sample = np.zeros(size)
    allreduce(sample)
    return sample

