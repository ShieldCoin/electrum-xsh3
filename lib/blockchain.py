# Electrum - lightweight Bitcoin client
# Copyright (C) 2012 thomasv@ecdsa.org
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import os
import threading

from . import util
from .bitcoin import Hash, hash_encode, int_to_hex, rev_hex
from . import constants
from .util import bfh, bh2u

from .blake2 import BLAKE2s as cblake

try:
    import scrypt
    getPoWHash = lambda x: scrypt.hash(x, x, N=1024, r=1, p=1, buflen=32)
except ImportError:
    util.print_msg("Warning: package scrypt not available; synchronization could be very slow")
    from .scrypt import scrypt_1024_1_1_80 as getPoWHash

MAX_TARGET = 0x00000FFFFF000000000000000000000000000000000000000000000000000000


class MissingHeader(Exception):
    pass


def serialize_header(res):
    s = int_to_hex(res.get('version'), 4) \
        + rev_hex(res.get('prev_block_hash')) \
        + rev_hex(res.get('merkle_root')) \
        + int_to_hex(int(res.get('timestamp')), 4) \
        + int_to_hex(int(res.get('bits')), 4) \
        + int_to_hex(int(res.get('nonce')), 4)
    return s

def deserialize_header(s, height):
    if not s:
        raise Exception('Invalid header: {}'.format(s))
    if len(s) != 80:
        raise Exception('Invalid header length: {}'.format(len(s)))
    hex_to_int = lambda s: int('0x' + bh2u(s[::-1]), 16)
    h = {}
    h['version'] = hex_to_int(s[0:4])
    h['prev_block_hash'] = hash_encode(s[4:36])
    h['merkle_root'] = hash_encode(s[36:68])
    h['timestamp'] = hex_to_int(s[68:72])
    h['bits'] = hex_to_int(s[72:76])
    h['nonce'] = hex_to_int(s[76:80])
    h['block_height'] = height
    return h

def hash_header(header):
    if header is None:
        return '0' * 64
    if header.get('prev_block_hash') is None:
        header['prev_block_hash'] = '00'*32
    return hash_encode(getPoWHash(bfh(serialize_header(header))))

def pow_hash_header(header):
    if header['version'] & (15 << 11) == (4 << 11): # blake python implementation
        blake_state = cblake()
        blake_state.update(bfh(serialize_header(header)))
        return hash_encode(blake_state.final())
    return hash_encode(getPoWHash(bfh(serialize_header(header)))) # default to scrypt


blockchains = {}

def read_blockchains(config):
    blockchains[0] = Blockchain(config, 0, None)
    fdir = os.path.join(util.get_headers_dir(config), 'forks')
    util.make_dir(fdir)
    l = filter(lambda x: x.startswith('fork_'), os.listdir(fdir))
    l = sorted(l, key = lambda x: int(x.split('_')[1]))
    for filename in l:
        checkpoint = int(filename.split('_')[2])
        parent_id = int(filename.split('_')[1])
        b = Blockchain(config, checkpoint, parent_id)
        h = b.read_header(b.checkpoint)
        if b.parent().can_connect(h, check_height=False):
            blockchains[b.checkpoint] = b
        else:
            util.print_error("cannot connect", filename)
    return blockchains

def check_header(header):
    if type(header) is not dict:
        return False
    for b in blockchains.values():
        if b.check_header(header):
            return b
    return False

def can_connect(header):
    for b in blockchains.values():
        if b.can_connect(header):
            return b
    return False


class Blockchain(util.PrintError):
    """
    Manages blockchain headers and their verification
    """

    def __init__(self, config, checkpoint, parent_id):
        self.config = config
        self.catch_up = None # interface catching up
        self.checkpoint = checkpoint
        self.checkpoints = constants.net.CHECKPOINTS
        self.parent_id = parent_id
        self.lock = threading.Lock()
        with self.lock:
            self.update_size()

    def parent(self):
        return blockchains[self.parent_id]

    def get_max_child(self):
        children = list(filter(lambda y: y.parent_id==self.checkpoint, blockchains.values()))
        return max([x.checkpoint for x in children]) if children else None

    def get_checkpoint(self):
        mc = self.get_max_child()
        return mc if mc is not None else self.checkpoint

    def get_branch_size(self):
        return self.height() - self.get_checkpoint() + 1

    def get_name(self):
        return self.get_hash(self.get_checkpoint()).lstrip('00')[0:10]

    def check_header(self, header):
        header_hash = hash_header(header)
        height = header.get('block_height')
        return header_hash == self.get_hash(height)

    def fork(parent, header):
        checkpoint = header.get('block_height')
        self = Blockchain(parent.config, checkpoint, parent.checkpoint)
        open(self.path(), 'w+').close()
        self.save_header(header)
        return self

    def height(self):
        return self.checkpoint + self.size() - 1

    def size(self):
        with self.lock:
            return self._size

    def update_size(self):
        p = self.path()
        self._size = os.path.getsize(p)//80 if os.path.exists(p) else 0


    def verify_header(self, header, prev_hash, target):
        _powhash = pow_hash_header(header)
        if prev_hash != header.get('prev_block_hash'):
            raise Exception("prev hash mismatch: %s vs %s" % (prev_hash, header.get('prev_block_hash')))
        if constants.net.TESTNET:
            return
        bits = self.target_to_bits(target)
        if bits != header.get('bits'):
            raise Exception("bits mismatch: %s vs %s" % (bits, header.get('bits')))
        algo_version = header['version'] & (15 << 11)
        if algo_version == (1  << 11) or algo_version == (4  << 11): # Only Scrypt, blake
            if int('0x' + _powhash, 16) > target:
                raise Exception("insufficient proof of work: %s vs target %s" % (int('0x' + _powhash, 16), target))

    def verify_chunk(self, index, data):
        num = len(data) // 80
        prev_hash = self.get_hash(index * 2016 - 1)
        for i in range(num):
            raw_header = data[i*80:(i+1) * 80]
            header = deserialize_header(raw_header, index*2016 + i)
            target = self.get_target(data, i, index)
            self.verify_header(header, prev_hash, target)
            prev_hash = hash_header(header)

    def path(self):
        d = util.get_headers_dir(self.config)
        filename = 'blockchain_headers' if self.parent_id is None else os.path.join('forks', 'fork_%d_%d'%(self.parent_id, self.checkpoint))
        return os.path.join(d, filename)

    def save_chunk(self, index, chunk):
        filename = self.path()
        d = (index * 2016 - self.checkpoint) * 80
        if d < 0:
            chunk = chunk[-d:]
            d = 0
        truncate = index >= len(self.checkpoints)
        self.write(chunk, d, truncate)
        self.swap_with_parent()

    def swap_with_parent(self):
        if self.parent_id is None:
            return
        parent_branch_size = self.parent().height() - self.checkpoint + 1
        if parent_branch_size >= self.size():
            return
        self.print_error("swap", self.checkpoint, self.parent_id)
        parent_id = self.parent_id
        checkpoint = self.checkpoint
        parent = self.parent()
        self.assert_headers_file_available(self.path())
        with open(self.path(), 'rb') as f:
            my_data = f.read()
        self.assert_headers_file_available(parent.path())
        with open(parent.path(), 'rb') as f:
            f.seek((checkpoint - parent.checkpoint)*80)
            parent_data = f.read(parent_branch_size*80)
        self.write(parent_data, 0)
        parent.write(my_data, (checkpoint - parent.checkpoint)*80)
        # store file path
        for b in blockchains.values():
            b.old_path = b.path()
        # swap parameters
        self.parent_id = parent.parent_id; parent.parent_id = parent_id
        self.checkpoint = parent.checkpoint; parent.checkpoint = checkpoint
        self._size = parent._size; parent._size = parent_branch_size
        # move files
        for b in blockchains.values():
            if b in [self, parent]: continue
            if b.old_path != b.path():
                self.print_error("renaming", b.old_path, b.path())
                os.rename(b.old_path, b.path())
        # update pointers
        blockchains[self.checkpoint] = self
        blockchains[parent.checkpoint] = parent

    def assert_headers_file_available(self, path):
        if os.path.exists(path):
            return
        elif not os.path.exists(util.get_headers_dir(self.config)):
            raise FileNotFoundError('Electrum headers_dir does not exist. Was it deleted while running?')
        else:
            raise FileNotFoundError('Cannot find headers file but headers_dir is there. Should be at {}'.format(path))

    def write(self, data, offset, truncate=True):
        filename = self.path()
        with self.lock:
            self.assert_headers_file_available(filename)
            with open(filename, 'rb+') as f:
                if truncate and offset != self._size*80:
                    f.seek(offset)
                    f.truncate()
                f.seek(offset)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self.update_size()

    def save_header(self, header):
        delta = header.get('block_height') - self.checkpoint
        data = bfh(serialize_header(header))
        assert delta == self.size()
        assert len(data) == 80
        self.write(data, delta*80)
        self.swap_with_parent()

    def read_header(self, height):
        assert self.parent_id != self.checkpoint
        if height < 0:
            return
        if height < self.checkpoint:
            return self.parent().read_header(height)
        if height > self.height():
            return
        delta = height - self.checkpoint
        name = self.path()
        self.assert_headers_file_available(name)
        with open(name, 'rb') as f:
            f.seek(delta * 80)
            h = f.read(80)
            if len(h) < 80:
                raise Exception('Expected to read a full header. This was only {} bytes'.format(len(h)))
        if h == bytes([0])*80:
            return None
        return deserialize_header(h, height)

    def get_hash(self, height):
        if height == -1:
            return '0000000000000000000000000000000000000000000000000000000000000000'
        elif height == 0:
            return constants.net.GENESIS
        elif height < len(self.checkpoints) * 2016:
            assert (height+1) % 2016 == 0, height
            index = height // 2016
            h, t, _ = self.checkpoints[index]
            return h
        else:
            return hash_header(self.read_header(height))

    def get_timestamp(self, height):
        if height < len(self.checkpoints) * 2016 and (height+1) % 2016 == 0:
            index = height // 2016
            _, _, ts = self.checkpoints[index]
            return ts
        return self.read_header(height).get('timestamp')

    def GetMaxClockDrift(self, Height):
        if (Height < 660000 or (Height < 817500 and Height > 800000)):
            return 2 * 60 * 60
        return 10 * 60

    def get_algo(self, header):
        switcher = {
            (1  << 11): 0, #scrypt
            (2  << 11): 4, # groestl
            (10 << 11): 2, # lyra
            (3  << 11): 1, # x17
            (4  << 11): 3, # blake
            (11 << 11): 5, # x16s
        }
        return switcher.get(header['version'] & (15 << 11), 0)

    def get_target(self, data, counter, index):
        height = index*2016 + counter
        raw = data[counter*80:(counter+1) * 80]
        cBlock = deserialize_header(raw, height)
        algo = self.get_algo(cBlock)

        T = 225
        FTL = self.GetMaxClockDrift(height)

        N = 60

        k = N*(N+1)*T/2
        sumTarget = 0
        t = 0
        j = 0

        samealgoblocks = []
        c = height - 1
        while c > 100 and len(samealgoblocks) <= N:
            if (c >= index*2016):
                tmpix = c - index*2016
                tmpraw = data[tmpix*80:(tmpix+1) * 80]
                block = deserialize_header(tmpraw, c)
            else:
                block = self.read_header(c)
            if self.get_algo(block) == algo:
                samealgoblocks.append(block)
            c-=1

        if c <= 100:
            return self.get_targetv1(height)

        # Loop through N most recent blocks.  "< height", not "<=". 
        # height-1 = most recently solved rblock
        for i in range(N, 0, -1):
            solvetime = samealgoblocks[i-1]['timestamp'] - samealgoblocks[i]['timestamp']
            solvetime = max(-FTL, min(solvetime, 6*T));
            j += 1
            t += solvetime * j
            target = self.bits_to_target(samealgoblocks[i-1].get('bits'))
            sumTarget += target / (k * N)

        # Keep t reasonable in case strange solvetimes occurred. 
        if t < k//10:
            t = k // 10

        next_target = t * sumTarget
        return int(next_target)

    def get_targetv1(self, index):
        # Old algo
        # compute target from chunk x, used in chunk x+1
        if constants.net.TESTNET:
            return 0
        if index == -1:
            return 0x00000FFFF0000000000000000000000000000000000000000000000000000000
        if index < len(self.checkpoints):
            h, t, _ = self.checkpoints[index]
            return t
        # new target
        # SHIELD: go back the full period unless it's the first retarget
        first_timestamp = self.get_timestamp(index * 2016 - 1 if index > 0 else 0)
        last = self.read_header(index * 2016 + 2015)
        if not first_timestamp or not last:
            raise MissingHeader()
        bits = last.get('bits')
        target = self.bits_to_target(bits)
        nActualTimespan = last.get('timestamp') - first_timestamp
        nTargetTimespan = 84 * 60 * 60
        nActualTimespan = max(nActualTimespan, nTargetTimespan // 4)
        nActualTimespan = min(nActualTimespan, nTargetTimespan * 4)
        new_target = min(MAX_TARGET, (target * nActualTimespan) // nTargetTimespan)
        return new_target

    def bits_to_target(self, bits):
        bitsN = (bits >> 24) & 0xff
        if not (bitsN >= 0x03 and bitsN <= 0x1e):
            raise Exception("First part of bits should be in [0x03, 0x1e]")
        bitsBase = bits & 0xffffff
        if not (bitsBase >= 0x8000 and bitsBase <= 0x7fffff):
            raise Exception("Second part of bits should be in [0x8000, 0x7fffff]")
        return bitsBase << (8 * (bitsN-3))

    def target_to_bits(self, target):
        c = ("%064x" % target)[2:]
        while c[:2] == '00' and len(c) > 6:
            c = c[2:]
        bitsN, bitsBase = len(c) // 2, int('0x' + c[:6], 16)
        if bitsBase >= 0x800000:
            bitsN += 1
            bitsBase >>= 8
        return bitsN << 24 | bitsBase

    def can_connect(self, header, check_height=True):
        if header is None:
            return False
        height = header['block_height']
        if check_height and self.height() != height - 1:
            #self.print_error("cannot connect at height", height)
            return False
        if height == 0:
            return hash_header(header) == constants.net.GENESIS
        try:
            prev_hash = self.get_hash(height - 1)
        except:
            return False
        if prev_hash != header.get('prev_block_hash'):
            return False
        return True

    def connect_chunk(self, idx, hexdata):
        try:
            data = bfh(hexdata)
            self.verify_chunk(idx, data)
            #self.print_error("validated chunk %d" % idx)
            self.save_chunk(idx, data)
            return True
        except BaseException as e:
            self.print_error('verify_chunk %d failed'%idx, str(e))
            return False

    def get_checkpoints(self):
        # for each chunk, store the hash of the last block and the target after the chunk
        cp = []
        n = self.height() // 2016
        for index in range(n):
            h = self.get_hash((index+1) * 2016 -1)
            target = self.get_target(index)
            # SHIELD: also store the timestamp of the last block
            tstamp = self.get_timestamp((index+1) * 2016 - 1)
            cp.append((h, target, tstamp))
        return cp
