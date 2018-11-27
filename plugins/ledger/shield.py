from btchip.btchip import btchip
from btchip.bitcoinTransaction import bitcoinTransaction
from struct import unpack
from btchip.bitcoinVarint import readVarint, writeVarint
from btchip.bitcoinTransaction import bitcoinInput, bitcoinOutput

class btchip_xsh(btchip):
    def getTrustedInput(self, transaction, index):
        result = {}
        # Header
        apdu = [ self.BTCHIP_CLA, self.BTCHIP_INS_GET_TRUSTED_INPUT, 0x00, 0x00 ]
        params = bytearray.fromhex("%.8x" % (index))
        params.extend(transaction.version)
        params.extend(transaction.time)
        writeVarint(len(transaction.inputs), params)
        apdu.append(len(params))
        apdu.extend(params)
        self.dongle.exchange(bytearray(apdu))
        # Each input
        for trinput in transaction.inputs:
                apdu = [ self.BTCHIP_CLA, self.BTCHIP_INS_GET_TRUSTED_INPUT, 0x80, 0x00 ]
                params = bytearray(trinput.prevOut)
                writeVarint(len(trinput.script), params)
                apdu.append(len(params))
                apdu.extend(params)
                self.dongle.exchange(bytearray(apdu))
                offset = 0
                while True:
                        blockLength = 251
                        if ((offset + blockLength) < len(trinput.script)):
                                dataLength = blockLength
                        else:
                                dataLength = len(trinput.script) - offset
                        params = bytearray(trinput.script[offset : offset + dataLength])
                        if ((offset + dataLength) == len(trinput.script)):
                                params.extend(trinput.sequence)
                        apdu = [ self.BTCHIP_CLA, self.BTCHIP_INS_GET_TRUSTED_INPUT, 0x80, 0x00, len(params) ]
                        apdu.extend(params)
                        self.dongle.exchange(bytearray(apdu))
                        offset += dataLength
                        if (offset >= len(trinput.script)):
                                break 
        # Number of outputs
        apdu = [ self.BTCHIP_CLA, self.BTCHIP_INS_GET_TRUSTED_INPUT, 0x80, 0x00 ]
        params = []
        writeVarint(len(transaction.outputs), params)
        apdu.append(len(params))
        apdu.extend(params)
        self.dongle.exchange(bytearray(apdu))
        # Each output
        indexOutput = 0
        for troutput in transaction.outputs:
                apdu = [ self.BTCHIP_CLA, self.BTCHIP_INS_GET_TRUSTED_INPUT, 0x80, 0x00 ]
                params = bytearray(troutput.amount)
                writeVarint(len(troutput.script), params)
                apdu.append(len(params))
                apdu.extend(params)
                self.dongle.exchange(bytearray(apdu))
                offset = 0
                while (offset < len(troutput.script)):
                        blockLength = 255
                        if ((offset + blockLength) < len(troutput.script)):
                                dataLength = blockLength
                        else:
                                dataLength = len(troutput.script) - offset
                        apdu = [ self.BTCHIP_CLA, self.BTCHIP_INS_GET_TRUSTED_INPUT, 0x80, 0x00, dataLength ]
                        apdu.extend(troutput.script[offset : offset + dataLength])
                        self.dongle.exchange(bytearray(apdu))
                        offset += dataLength
        # Locktime
        apdu = [ self.BTCHIP_CLA, self.BTCHIP_INS_GET_TRUSTED_INPUT, 0x80, 0x00, len(transaction.lockTime) ]
        apdu.extend(transaction.lockTime)
        response = self.dongle.exchange(bytearray(apdu))
        result['trustedInput'] = True
        result['value'] = response
        return result

class shieldTransaction(bitcoinTransaction):
    def __init__(self, data=None):
        self.version = ""
        self.time = ""
        self.inputs = []
        self.outputs = []
        self.lockTime = ""
        self.witness = False
        self.witnessScript = ""
        if data is not None:
                offset = 0
                self.version = data[offset:offset + 4]
                offset += 4
                if self.version[0] != 0x04:
                        checktime = unpack("<L", data[offset:offset + 4])[0]
                        if (checktime >= 1507311610) and (checktime <= 1540188138):
                                self.time = data[offset:offset + 4]
                                offset += 4
                if (data[offset] == 0) and (data[offset + 1] != 0):
                        offset += 2
                        self.witness = True
                inputSize = readVarint(data, offset)
                offset += inputSize['size']
                numInputs = inputSize['value']
                for i in range(numInputs):
                        tmp = { 'buffer': data, 'offset' : offset}
                        self.inputs.append(bitcoinInput(tmp))
                        offset = tmp['offset']
                outputSize = readVarint(data, offset)
                offset += outputSize['size']
                numOutputs = outputSize['value']
                for i in range(numOutputs):
                        tmp = { 'buffer': data, 'offset' : offset}
                        self.outputs.append(bitcoinOutput(tmp))
                        offset = tmp['offset']
                if self.witness:
                        self.witnessScript = data[offset : len(data) - 4]
                        self.lockTime = data[len(data) - 4:]
                else:
                        self.lockTime = data[offset:offset + 4]
