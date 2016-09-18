#!/usr/bin/env python
# coding=utf-8
import requests
import socket
import struct
import time
import select
import re
import json
import threading
import traceback
import sys

class _socket(socket.socket):
    def communicate(self, data):
        self.push(data)
        return self.pull()
    def push(self, data):
        s = struct.pack('i', 9 + len(data)) * 2
        s += b'\xb1\x02\x00\x00' # 689
        s += data.encode('ascii') + b'\x00'
        self.sendall(s)
    def pull(self):
        try: # for socket.settimeout
            return self.recv(9999)
        except Exception as e:
            print(str(e.args))
            return ''

class DouYuDamMuClient(object):
    __functionDict = {'default': lambda x: 0}
    def __init__(self, url, maxNoDanMuWait = 180, anchorStatusRescanTime = 30):
        self.url = url
        self.maxNoDanMuWait = maxNoDanMuWait
        self.anchorStatusRescanTime = anchorStatusRescanTime
        self.deprecated = False
        self.live = False
        self.danmuSocket = None
        self.danmuThread, self.heartThread = None, None
        self.msgPipe = []
        self.danmuWaitTime = -1
        self.__isRunning = False
    
    def __register(self, fn, msgType):
        if fn is None:
            if msgType == 'default':
                self.__functionDict['default'] = lambda x: 0
            elif self.__functionDict.get(msgType):
                del self.__functionDict[msgType]
        else:
            self.__functionDict[msgType] = fn

    def danmu(self, fn):
        self.__register(fn, 'danmu')
        return fn

    def gift(self, fn):
        self.__register(fn, 'gift')
        return fn

    def other(self, fn):
        self.__register(fn, 'other')
        return fn

    def _prepare_env(self):
        return ('openbarrage.douyutv.com', 8601), {'room_id': self.roomId} 

    def _get_live_status(self):
        url = 'http://open.douyucdn.cn/api/RoomApi/room/%s' % (self.url.split('/')[-1] or self.url.split('/')[-2])
        j = requests.get(url).json()
        #print j['data'].get('room_status')
        if j.get('error') != 0 or j['data'].get('room_status') != '1':
            return False
        self.roomId = j['data']['room_id']
        return True 

    def _init_socket(self, danmu, roomInfo):
        self.danmuSocket = _socket()
        self.danmuSocket.connect(danmu)
        self.danmuSocket.settimeout(3)
        self.danmuSocket.communicate('type@=loginreq/roomid@=%s/'%roomInfo['room_id'])
        self.danmuSocket.push('type@=joingroup/rid@=%s/gid@=-9999/'%roomInfo['room_id'])

    def _create_thread_fn(self, roomInfo):
        def keep_alive(self):
            self.danmuSocket.push('type@=keeplive/tick@=%s/'%int(time.time()))
            time.sleep(30)
        def get_danmu(self):
            if not select.select([self.danmuSocket], [], [], 1)[0]:
                return
            content = self.danmuSocket.pull()
            for msg in re.findall(b'(type@=.*?)\x00', content):
                try:
                    msg = msg.replace(b'@=', b'":"').replace(b'/', b'","')
                    msg = msg.replace(b'@A', b'@').replace(b'@S', b'/')
                    msg = json.loads((b'{"' + msg[:-2] + b'}').decode('utf8', 'ignore'))
                    msg['NickName'] = msg.get('nn', '')
                    msg['Content']  = msg.get('txt', '')
                    msg['MsgType']  = {'dgb': 'gift', 'chatmsg': 'danmu', 'uenter': 'enter'}.get(msg['type'], 'other')
                except Exception as e:
                    pass
                else:
                    self.danmuWaitTime = time.time() + self.maxNoDanMuWait
                    self.msgPipe.append(msg)
        return get_danmu, keep_alive

    def thread_alive(self):
        if self.danmuSocket is None or not self.danmuThread.isAlive():
            return False
        else:
            return True
    
    def _socket_timeout(self, fn):
        def __socket_timeout(*args, **kwargs):
            try:
                fn(*args, **kwargs)
            except Exception as e:
                print(str(e.args))
                if not self.live:
                    return
                self.live = False
                waitEndTime = time.time() + 20
                while self.thread_alive() and time.time() < waitEndTime:
                    time.sleep(1)
                self.start()
        return __socket_timeout

    def _wrap_thread(self, danmuThreadFn, heartThreadFn):
        @self._socket_timeout
        def heart_beat(self):
            while self.live and not self.deprecated:
                heartThreadFn(self)

        @self._socket_timeout
        def get_danmu(self):
            while self.live and not self.deprecated:
                if self.danmuWaitTime != -1 and self.danmuWaitTime < time.time():
                    raise Exception('No dammu received in %ss'%self.maxNoDanMuWait)
                danmuThreadFn(self)
        
        self.heartThread = threading.Thread(target = heart_beat, args = (self,))
        self.heartThread.setDaemon(True)
        self.danmuThread = threading.Thread(target = get_danmu, args = (self,))
        self.danmuThread.setDaemon(True)

    def _start_receive(self):
        self.live = True
        self.danmuThread.start()
        self.heartThread.start()
        self.danmuWaitTime = time.time() + 20

    def _start_fn(self):
        print 'start'
        while not self.deprecated:
            try:
                while not self.deprecated:
                    if self._get_live_status():
                        break
                else:
                    break
                danmuSocketInfo, roomInfo = self._prepare_env()
                if self.danmuSocket:
                    self.danmuSocket.close()
                self.danmuWaitTime = -1
                self._init_socket(danmuSocketInfo, roomInfo)
                danmuThreadFn, heartThreadFn = self._create_thread_fn(roomInfo)
                self._wrap_thread(danmuThreadFn, heartThreadFn)
                self._start_receive()
            except Exception as e:
                print(str(e.args))
                time.sleep(5)
            else:
                break

    def start(self, blockThread = False, pauseTime = .1):
        self.__isRunning = True
        receiveThread = threading.Thread(target=self._start_fn)
        receiveThread.setDaemon(True)
        receiveThread.start()
        def _start():
            while self.__isRunning:
                if self.msgPipe:
                    msg = self.msgPipe.pop()
                    fn = self.__functionDict.get(msg['MsgType'], self.__functionDict['default'])
                    try:
                        fn(msg)
                    except:
                        traceback.print_exc()
                else:
                    time.sleep(pauseTime)
        if blockThread:
            try:
                _start()
            except KeyboardInterrupt:
                print('Bye~')
        else:
            danmuThread = threading.Thread(target = _start)
            danmuThread = threading.setDaemon(True)
            danmuThread.start()


def pp(msg):
    print(msg.encode(sys.stdin.encoding, 'ignore').
        decode(sys.stdin.encoding))

if __name__ == '__main__':
    url = 'https://www.douyu.com/xiongda'
    danmu = DouYuDamMuClient(url)

    @danmu.danmu
    def danmu_fn(msg):
        pp('[%s] %s' % (msg['NickName'], msg['Content']))

    @danmu.gift
    def gift_fn(msg):
        pp('[%s] sent a gift!' % msg['NickName'])

    @danmu.other
    def other_fn(msg):
        pp('Other message received')

    print 'Init'
    danmu.start(blockThread = True)
