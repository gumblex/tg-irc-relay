#!/usr/bin/env python

'''A Python module that allows you to connect to IRC in a simple way.'''

import errno
import socket
import select
import ssl
import sys
import threading
import time

__all__ = ['IRCConnection', 'IRCClient']

DEFAULT_BUFFER_LENGTH = 1024

if sys.version_info >= (3,):
    tostr = str
else:
    tostr = unicode


def stripcomma(s):
    '''Delete the comma if the string starts with a comma.'''
    s = tostr(s)
    if s.startswith(':'):
        return s[1:]
    else:
        return s


def tolist(s, f=None):
    if f == None:
        try:
            if isinstance(s, (str, tostr)):
                return [s]
            elif isinstance(s, (tuple, list)):
                return s
            else:
                return list(s)
        except TypeError:
            return [tostr(s)]
    else:
        return list(map(f, tolist(s)))


def catchannel(s):
    return ','.join(tolist(s, rmnlsp))


def rmnl(s):
    '''Replace \\n with spaces from a string.'''
    return tostr(s).replace('\r', '').strip('\n').replace('\n', ' ')


def rmnlsp(s):
    '''Remove \\n and spaces from a string.'''
    return tostr(s).replace('\r', '').replace('\n', '').replace(' ', '')


def rmcr(s):
    '''Remove \\r from a string.'''
    return tostr(s).replace('\r', '')


class IRCConnection:

    def __init__(self):
        self.addr = None
        self.nick = None
        self.sock = None
        self.recvbuf = b''
        self.sendbuf = b''
        self.buffer_length = DEFAULT_BUFFER_LENGTH
        self.lock = threading.RLock()
        self.recvlock = threading.RLock()

    def acquire_lock(self, blocking=True):
        if self.lock.acquire(blocking):
            return True
        elif blocking:
            raise threading.ThreadError('Cannot acquire lock.')
        else:
            return False

    def connect(self, addr=('irc.freenode.net', 6667), use_ssl=False):
        '''Connect to a IRC server. addr is a tuple of (server, port)'''
        self.acquire_lock()
        try:
            self.addr = (rmnlsp(addr[0]), addr[1])
            if use_ssl:
                if (3,) <= sys.version_info < (3, 3):
                    self.sock = ssl.SSLSocket()
                else:
                    self.sock = ssl.SSLSocket(sock=socket.socket())
            else:
                self.sock = socket.socket()
            self.sock.settimeout(300)
            self.sock.connect(self.addr)
            self.nick = None
            self.recvbuf = b''
            self.sendbuf = b''
        finally:
            self.lock.release()

    def quote(self, s, sendnow=True):
        '''Send a raw IRC command. Split multiple commands using \\n.'''
        tmpbuf = b''
        for i in s.splitlines():
            if i:
                tmpbuf += i.encode('utf-8', 'replace') + b'\r\n'
        if tmpbuf:
            if sendnow:
                self.send(tmpbuf)
            else:
                self.sendbuf += tmpbuf

    def send(self, sendbuf=None):
        '''Flush the send buffer.'''
        self.acquire_lock()
        try:
            if not self.sock:
                e = socket.error(
                    '[errno %d] Socket operation on non-socket' % errno.ENOTSOCK)
                e.errno = errno.ENOTSOCK
                raise e
            try:
                if sendbuf == None:
                    if self.sendbuf:
                        self.sock.sendall(self.sendbuf)
                    self.sendbuf = b''
                elif sendbuf:
                    self.sock.sendall(sendbuf)
            except socket.error as e:
                try:
                    self.sock.close()
                finally:
                    self.sock = None
                raise
                self.quit('Network error.', wait=False)
        finally:
            self.lock.release()

    def setpass(self, passwd, sendnow=True):
        '''Send password, it should be used before setnick().\nThis password is different from that one sent to NickServ and it is usually unnecessary.'''
        self.quote('PASS %s' % rmnl(passwd), sendnow=sendnow)

    def setnick(self, newnick, sendnow=True):
        '''Set nickname.'''
        self.nick = rmnlsp(newnick)
        self.quote('NICK %s' % self.nick, sendnow=sendnow)

    def setuser(self, ident=None, realname=None, sendnow=True):
        '''Set user ident and real name.'''
        if ident == None:
            ident = self.nick
        if realname == None:
            realname = ident
        self.quote('USER %s %s %s :%s' % (rmnlsp(ident), rmnlsp(
            ident), rmnlsp(self.addr[0]), rmnl(realname)), sendnow=sendnow)

    def join(self, channel, key=None, sendnow=True):
        '''Join channel. A password is optional.'''
        if key != None:
            key = ' ' + key
        else:
            key = ''
        self.quote('JOIN %s%s' %
                   (catchannel(channel), rmnl(key)), sendnow=sendnow)

    def part(self, channel, reason=None, sendnow=True):
        '''Leave channel. A reason is optional.'''
        if reason != None:
            reason = ' :' + reason
        else:
            reason = ''
        self.quote('PART %s%s' %
                   (catchannel(channel), rmnl(reason)), sendnow=sendnow)

    def quit(self, reason=None, wait=True):
        '''Quit and disconnect from server. A reason is optional. If wait is True, the send buffer will be flushed.'''
        if reason != None:
            reason = ' :' + reason
        else:
            reason = ''
        self.acquire_lock()
        try:
            if self.sock:
                try:
                    if wait:
                        self.quote('QUIT%s' % rmnl(reason), sendnow=False)
                        self.send()
                    else:
                        self.quote('QUIT%s' % rmnl(reason), sendnow=True)
                except:
                    pass
                time.sleep(2)
                try:
                    self.sock.close()
                except:
                    pass
            self.sendbuf = b''
            self.sock = None
            self.addr = None
            self.nick = None
        finally:
            self.lock.release()

    def say(self, dest, msg, sendnow=True):
        '''Send a message to a channel, or a private message to a person.'''
        tmpbuf = ''
        for i in msg.splitlines():
            tmpbuf += 'PRIVMSG %s :%s\n' % (catchannel(dest), rmcr(i))
        self.quote(tmpbuf, sendnow=sendnow)

    def me(self, dest, action, sendnow=True):
        '''Send an action message.'''
        tmpbuf = ''
        for i in action.splitlines():
            tmpbuf += '\x01ACTION %s\x01' % i
        self.say(dest, tmpbuf, sendnow=sendnow)

    def mode(self, target, newmode=None, sendnow=True):
        '''Read or set mode of a nick or a channel.'''
        if newmode != None:
            if target.startswith('#') or target.startswith('&'):
                newmode = ' ' + newmode
            else:
                newmode = ' :' + newmode
        else:
            newmode = ''
        self.quote('MODE %s%s' %
                   (rmnlsp(target), rmnl(newmode)), sendnow=sendnow)

    def kick(self, channel, target, reason=None, sendnow=True):
        '''Kick a person out of the channel.'''
        if reason != None:
            reason = ' :' + reason
        else:
            reason = ''
        self.quote('KICK %s %s%s' % (
            rmnlsp(channel), rmnlsp(target), rmnl(reason)), sendnow=sendnow)

    def away(self, state=None, sendnow=True):
        '''Set away status with an argument, or cancal away status without the argument'''
        if state != None:
            state = ' :' + state
        else:
            state = ''
        self.quote('AWAY%s' % rmnl(state), sendnow=sendnow)

    def invite(self, target, channel, sendnow=True):
        '''Invite a specific user to an invite-only channel.'''
        self.quote('INVITE %s %s' %
                   (rmnlsp(target), rmnlsp(channel)), sendnow=sendnow)

    def notice(self, dest, msg=None, sendnow=True):
        '''Send a notice to a specific user.'''
        if msg != None:
            tmpbuf = ''
            for i in msg.splitlines():
                if i:
                    tmpbuf += 'NOTICE %s :%s' % (rmnlsp(dest), rmcr(i))
                else:
                    tmpbuf += 'NOTICE %s' % rmnlsp(dest)
            self.quote(tmpbuf, sendnow=sendnow)
        else:
            self.quote('NOTICE %s' % rmnlsp(dest), sendnow=sendnow)

    def topic(self, channel, newtopic=None, sendnow=True):
        '''Set a new topic or get the current topic.'''
        if newtopic != None:
            newtopic = ' :' + newtopic
        else:
            newtopic = ''
        self.quote('TOPIC %s%s' %
                   (rmnlsp(channel), rmnl(newtopic)), sendnow=sendnow)

    def recv(self, block=True):
        '''Receive stream from server.\nDo not call it directly, it should be called by parse() or recvline().'''
        if self.recvlock.acquire():
            try:
                if not self.sock:
                    e = socket.error(
                        '[errno %d] Socket operation on non-socket' % errno.ENOTSOCK)
                    e.errno = errno.ENOTSOCK
                    raise e
                try:
                    received = b''
                    if block:
                        received = self.sock.recv(self.buffer_length)
                    else:
                        oldtimeout = self.sock.gettimeout()
                        self.sock.settimeout(0)
                        try:
                            if isinstance(self.sock, ssl.SSLSocket):
                                received = self.sock.recv(self.buffer_length)
                            else:
                                received = self.sock.recv(
                                    self.buffer_length, socket.MSG_DONTWAIT)
                        except ssl.SSLWantReadError:
                            select.select([self.sock], [], [])
                            received = self.sock.recv(self.buffer_length)
                        except ssl.SSLWantWriteError:
                            select.select([], [self.sock], [])
                            received = self.sock.recv(self.buffer_length)
                        finally:
                            self.sock.settimeout(oldtimeout)
                            del oldtimeout
                    if received:
                        self.recvbuf += received
                    else:
                        self.quit('Connection reset by peer.', wait=False)
                    return True
                except socket.timeout as e:
                    try:
                        self.quit('Operation timed out.', wait=False)
                    finally:
                        self.sock = None
                    raise
                except socket.error as e:
                    if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        return False
                    else:
                        try:
                            self.quit('Network error.', wait=False)
                        finally:
                            self.sock = None
                        raise
            finally:
                self.recvlock.release()
        elif block:
            raise threading.ThreadError('Cannot acquire lock.')
        else:
            return False

    def recvline(self, block=True):
        '''Receive a raw line from server.\nIt calls recv(), and is called by parse() when line==None.\nIts output can be the 'line' argument of parse()'s input.'''
        if self.recvlock.acquire(blocking=block):
            try:
                while self.recvbuf.find(b'\n') == -1 and self.recv(block):
                    pass
                if self.recvbuf.find(b'\n') != -1:
                    line, self.recvbuf = self.recvbuf.split(b'\n', 1)
                    return line.rstrip(b'\r').decode('utf-8', 'replace')
                else:
                    return None
            finally:
                self.recvlock.release()
        else:
            return None

    def parse(self, block=True, line=None):
        '''Receive messages from server and process it.\nReturning a dictionary or None.\nIts 'line' argument accepts the output of recvline().'''
        if line == None:
            line = self.recvline(block)
        if line:
            try:
                if line.startswith('PING '):
                    try:
                        self.quote('PONG %s' % line[5:], sendnow=True)
                    finally:
                        return {'nick': None, 'ident': None, 'cmd': 'PING', 'dest': None, 'msg': stripcomma(line[5:])}
                if line.startswith(':'):
                    cmd = line.split(' ', 1)
                    nick = cmd.pop(0).split('!', 1)
                    if len(nick) >= 2:
                        nick, ident = nick
                    else:
                        ident = None
                        nick = nick[0]
                    nick = stripcomma(nick)
                else:
                    nick = None
                    ident = None
                    if line == "":
                        cmd = []
                    else:
                        cmd = [line]
                if cmd != []:
                    msg = cmd[0].split(' ', 1)
                    cmd = msg.pop(0)
                    if msg != []:
                        if msg[0].startswith(':'):
                            dest = None
                            msg = stripcomma(msg[0])
                        else:
                            msg = msg[0].split(' ', 1)
                            dest = msg.pop(0)
                            if cmd != 'KICK':
                                if msg != []:
                                    msg = stripcomma(msg[0])
                                else:
                                    msg = None
                            else:
                                if msg != []:
                                    msg = msg[0].split(' ', 1)
                                    dest2 = msg.pop(0)
                                    if msg != []:
                                        msg = stripcomma(msg[0])
                                    else:
                                        msg = None
                                    dest = (dest, dest2)
                                else:
                                    msg = None
                                    dest = (None, dest)
                    else:
                        msg = dest = None
                else:
                    msg = dest = cmd = None
                try:
                    if nick and cmd == 'PRIVMSG' and msg and tostr(msg).startswith('\x01PING '):
                        self.notice(tostr(nick), tostr(msg), sendnow=True)
                finally:
                    return {'nick': nick, 'ident': ident, 'cmd': cmd, 'dest': dest, 'msg': msg}
            except:
                return {'nick': None, 'ident': None, 'cmd': None, 'dest': None, 'msg': line}
        else:
            return None

    def __del__(self):
        if self.sock:
            self.quit(wait=False)


class IRCClient:

    def __init__(self):
        self.connection = IRCConnection()
        self.handlers = {}
        self.roaster = {}

    def connect(self, addr, nick, ident=None, realname=None):
        self.connection.connect(addr)
        self.setnick(nick)
        self.setuser(ident, realname)

    def quit(self, reason=None, wait=True):
        self.connection.quit()

# vim: et ft=python sts=4 sw=4 ts=4
