#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#  Copyright 2015 Dingyuan Wang
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as published
#  by the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import re
import sys
import time
import json
import queue
import logging
import threading
import functools
import collections

import libirc
import requests

__version__ = '1.2'

MEDIA_TYPES = frozenset(('audio', 'document', 'photo', 'sticker', 'video', 'voice', 'contact', 'location', 'new_chat_participant', 'left_chat_participant', 'new_chat_title', 'new_chat_photo', 'delete_chat_photo', 'group_chat_created'))
EXT_MEDIA_TYPES = frozenset(('audio', 'document', 'photo', 'sticker', 'video', 'voice', 'contact', 'location', 'new_chat_participant', 'left_chat_participant', 'new_chat_title', 'new_chat_photo', 'delete_chat_photo', 'group_chat_created', '_ircuser'))

loglevel = logging.DEBUG if sys.argv[-1] == '-d' else logging.INFO

logging.basicConfig(stream=sys.stdout, format='# %(asctime)s [%(levelname)s] %(message)s', level=loglevel)

HSession = requests.Session()
USERAGENT = 'TgIRCRelay/%s %s' % (__version__, HSession.headers["User-Agent"])
HSession.headers["User-Agent"] = USERAGENT

re_ircaction = re.compile('^\x01ACTION (.*)\x01$')
re_ircforward = re.compile(r'^\[([^]]+)\] (.*)$|^\*\* ([^ ]+) (.*) \*\*$')

class LRUCache:

    def __init__(self, maxlen):
        self.capacity = maxlen
        self.cache = collections.OrderedDict()

    def __getitem__(self, key):
        value = self.cache.pop(key)
        self.cache[key] = value
        return value

    def get(self, key, default=None):
        try:
            value = self.cache.pop(key)
            self.cache[key] = value
            return value
        except KeyError:
            return default

    def __setitem__(self, key, value):
        try:
            self.cache.pop(key)
        except KeyError:
            if len(self.cache) >= self.capacity:
                self.cache.popitem(last=False)
        self.cache[key] = value

def async_func(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        def func_noerr(*args, **kwargs):
            try:
                func(*args, **kwargs)
            except Exception:
                logging.exception('Async function failed.')
        thr = threading.Thread(target=func_noerr, args=args, kwargs=kwargs)
        thr.daemon = True
        thr.start()
    return wrapped

def _raise_ex(ex):
    raise ex

### Polling

def getupdates():
    global CFG, MSG_Q
    while 1:
        try:
            updates = bot_api('getUpdates', offset=CFG['offset'], timeout=10)
        except Exception as ex:
            logging.exception('Get updates failed.')
            continue
        if updates:
            logging.debug('Messages coming.')
            CFG['offset'] = updates[-1]["update_id"] + 1
            for upd in updates:
                MSG_Q.put(upd)
        time.sleep(.2)

def checkircconn():
    global ircconn
    if not ircconn or not ircconn.sock:
        ircconn = libirc.IRCConnection()
        ircconn.connect((CFG['ircserver'], CFG['ircport']), use_ssl=CFG['ircssl'])
        if CFG.get('ircpass'):
            ircconn.setpass(CFG['ircpass'])
        ircconn.setnick(CFG['ircnick'])
        ircconn.setuser(CFG['ircnick'], CFG['ircnick'])
        ircconn.join(CFG['ircchannel'])
        logging.info('IRC (re)connected.')

def getircupd():
    global MSG_Q
    while 1:
        checkircconn()
        line = ircconn.parse(block=False)
        if line and line["cmd"] == "PRIVMSG":
            if line["dest"] != CFG['ircnick'] and not re.match(CFG['ircbanre'], line["nick"]):
                updateid = -int(time.time())
                msg = {
                    'message_id': updateid,
                    'from': {'id': CFG['ircbotid'], 'first_name': CFG['ircbotname'], 'username': 'orzirc_bot'},
                    'date': int(time.time()),
                    'chat': {'id': -CFG['groupid'], 'title': CFG['ircchannel']},
                    'text': line["msg"].strip(),
                    '_ircuser': line["nick"]
                }
                MSG_Q.put({'update_id': updateid, 'message': msg})
        time.sleep(.5)

def irc_send(text='', reply_to_message_id=None):
    if ircconn:
        checkircconn()
        if reply_to_message_id:
            m = MSG_CACHE.get(reply_to_message_id, {})
            logging.debug('Got reply message: ' + str(m))
            if '_ircuser' in m:
                text = "%s: %s" % (m['_ircuser'], text)
            elif 'from' in m:
                src = dc_getufname(m['from'])[:20]
                if m['from']['id'] in (CFG['botid'], CFG['ircbotid']):
                    rnmatch = re_ircforward.match(m.get('text', ''))
                    if rnmatch:
                        src = rnmatch.group(1) or src
                text = "%s: %s" % (src, text)
        text = text.strip()
        if text.count('\n') < 1:
            ircconn.say(CFG['ircchannel'], text)

@async_func
def irc_forward(msg):
    if not ircconn:
        return
    try:
        if msg['from']['id'] == CFG['ircbotid']:
            return
        checkircconn()
        text = msg.get('text', '')
        mkeys = tuple(msg.keys() & MEDIA_TYPES)
        if mkeys:
            if text:
                text += ' ' + servemedia(msg)
            else:
                text = servemedia(msg)
        if text and not text.startswith('@@@'):
            if 'forward_from' in msg:
                fwdname = ''
                if msg['forward_from']['id'] in (CFG['botid'], CFG['ircbotid']):
                    rnmatch = re_ircforward.match(msg.get('text', ''))
                    if rnmatch:
                        fwdname = rnmatch.group(1) or rnmatch.group(3)
                        text = rnmatch.group(2) or rnmatch.group(4)
                fwdname = fwdname or dc_getufname(msg['forward_from'])[:20]
                text = "Fwd %s: %s" % (fwdname, text)
            elif 'reply_to_message' in msg:
                replname = ''
                replyu = msg['reply_to_message']['from']
                if replyu['id'] in (CFG['botid'], CFG['ircbotid']):
                    rnmatch = re_ircforward.match(msg['reply_to_message'].get('text', ''))
                    if rnmatch:
                        replname = rnmatch.group(1) or rnmatch.group(3)
                replname = replname or dc_getufname(replyu)[:20]
                text = "%s: %s" % (replname, text)
            # ignore blank lines
            text = list(filter(lambda s: s.strip(), text.splitlines()))
            if len(text) > 3:
                text = text[:3]
                text[-1] += ' [...]'
            for ln in text[:3]:
                ircconn.say(CFG['ircchannel'], '[%s] %s' % (dc_getufname(msg['from'])[:20], ln))
    except Exception:
        logging.exception('Forward a message to IRC failed.')


### API Related

class BotAPIFailed(Exception):
    pass

def change_session():
    global HSession
    HSession.close()
    HSession = requests.Session()
    HSession.headers["User-Agent"] = USERAGENT
    logging.warning('Session changed.')

def bot_api(method, **params):
    for att in range(3):
        try:
            req = HSession.get(URL + method, params=params)
            retjson = req.content
            ret = json.loads(retjson.decode('utf-8'))
            break
        except Exception as ex:
            if att < 1:
                time.sleep((att+1) * 2)
                change_session()
            else:
                raise ex
    if not ret['ok']:
        raise BotAPIFailed(repr(ret))
    return ret['result']

def bot_api_noerr(method, **params):
    try:
        bot_api(method, **params)
    except Exception:
        logging.exception('Async bot API failed.')

def sync_sendmsg(text, chat_id, reply_to_message_id=None):
    text = text.strip()
    if not text:
        logging.warning('Empty message ignored: %s, %s' % (chat_id, reply_to_message_id))
        return
    logging.info('sendMessage(%s): %s' % (len(text), text[:20]))
    if len(text) > 2000:
        text = text[:1999] + '…'
    reply_id = reply_to_message_id
    if reply_to_message_id and reply_to_message_id < 0:
        reply_id = None
    m = bot_api('sendMessage', chat_id=chat_id, text=text, reply_to_message_id=reply_id)
    if chat_id == -CFG['groupid']:
        MSG_CACHE[m['message_id']] = m
        # IRC messages
        if reply_to_message_id is not None:
            irc_send(text, reply_to_message_id)
    return m

sendmsg = async_func(sync_sendmsg)

@async_func
def typing(chat_id):
    logging.info('sendChatAction: %r' % chat_id)
    bot_api('sendChatAction', chat_id=chat_id, action='typing')

def getfile(file_id):
    logging.info('getFile: %r' % file_id)
    return bot_api('getFile', file_id=file_id)

def retrieve(url, filename, raisestatus=True):
    # NOTE the stream=True parameter
    r = requests.get(url, stream=True)
    if raisestatus:
        r.raise_for_status()
    with open(filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk: # filter out keep-alive new chunks
                f.write(chunk)
        f.flush()
    return r.status_code

def classify(msg):
    '''
    Classify message type:

    - Command: (0)
            All messages that start with a slash ‘/’ (see Commands above)
            Messages that @mention the bot by username
            Replies to the bot's own messages

    - Group message (1)
    - IRC message (2)
    - new_chat_participant (3)
    - Ignored message (10)
    - Invalid calling (-1)
    '''
    chat = msg['chat']
    text = msg.get('text', '').strip()
    if text:
        if text[0] in "/'" or ('@' + CFG['botname']) in text:
            return 0
        elif 'first_name' in chat:
            return 0
        else:
            reply = msg.get('reply_to_message')
            if reply and reply['from']['id'] == CFG['botid']:
                return 0

    # If not enabled, there won't be this kind of msg
    ircu = msg.get('_ircuser')
    if ircu and ircu != CFG['ircnick']:
        return 2

    if 'title' in chat:
        # Group chat
        if 'new_chat_participant' in msg:
            return 3
        if chat['id'] == -CFG['groupid']:
            if msg['from']['id'] == CFG['botid']:
                return 10
            else:
                return 1
        else:
            return 10
    else:
        return -1

def command(text, chatid, replyid, msg):
    try:
        t = text.strip().split(' ')
        if not t:
            return
        if t[0][0] in "/'":
            cmd = t[0][1:].lower().replace('@' + CFG['botname'], '')
            if cmd in COMMANDS:
                if chatid > 0 or chatid == -CFG['groupid'] or cmd in PUBLIC:
                    expr = ' '.join(t[1:]).strip()
                    logging.info('Command: /%s %s' % (cmd, expr[:20]))
                    COMMANDS[cmd](expr, chatid, replyid, msg)
            elif chatid > 0:
                sendmsg('Invalid command. Send /help for help.', chatid, replyid)
        # 233333
        #elif all(n.isdigit() for n in t):
            #COMMANDS['m'](' '.join(t), chatid, replyid, msg)
        elif chatid > 0:
            t = ' '.join(t).strip()
            logging.info('Reply: ' + t[:20])
            COMMANDS['reply'](t, chatid, replyid, msg)
    except Exception:
        logging.exception('Excute command failed.')

def processmsg():
    d = MSG_Q.get()
    logging.debug('Msg arrived: %r' % d)
    uid = d['update_id']
    if 'message' in d:
        msg = d['message']
        if 'text' in msg:
            msg['text'] = msg['text'].replace('\xa0', ' ')
        elif 'caption' in msg:
            msg['text'] = msg['caption'].replace('\xa0', ' ')
        MSG_CACHE[msg['message_id']] = msg
        cls = classify(msg)
        logging.debug('Classified as: %s', cls)
        if msg['chat']['id'] == -CFG['groupid'] and CFG.get('t2i'):
            irc_forward(msg)
        if cls == 0:
            rid = msg['message_id']
            if CFG.get('i2t') and '_ircuser' in msg:
                rid = sync_sendmsg('[%s] %s' % (msg['_ircuser'], msg['text']), msg['chat']['id'])['message_id']
            command(msg['text'], msg['chat']['id'], rid, msg)
        elif cls == 2:
            if CFG.get('i2t'):
                act = re_ircaction.match(msg['text'])
                if act:
                    sendmsg('** %s %s **' % (msg['_ircuser'], act.group(1)), msg['chat']['id'])
                else:
                    sendmsg('[%s] %s' % (msg['_ircuser'], msg['text']), msg['chat']['id'])
        elif cls == -1:
            sendmsg('Wrong usage', msg['chat']['id'], msg['message_id'])

def cachemedia(msg):
    '''
    Download specified media if not exist.
    '''
    mt = msg.keys() & frozenset(('audio', 'document', 'sticker', 'video', 'voice'))
    file_ext = ''
    if mt:
        file_id = msg[mt]['file_id']
        file_size = msg[mt].get('file_size')
        if mt == 'sticker':
            file_ext = '.webp'
    elif 'photo' in msg:
        photo = max(msg['photo'], key=lambda x: x['width'])
        file_id = photo['file_id']
        file_size = photo.get('file_size')
        file_ext = '.jpg'
    fp = getfile(file_id)
    file_size = fp.get('file_size') or file_size
    file_path = fp.get('file_path')
    if not file_path:
        raise BotAPIFailed("can't get file_path for " + file_id)
    file_ext = os.path.splitext(file_path)[1] or file_ext
    cachename = file_id + file_ext
    fpath = os.path.join(CFG['cachepath'], cachename)
    try:
        if os.path.isfile(fpath) and os.path.getsize(fpath) == file_size:
            return (cachename, 304)
    except Exception:
        pass
    return (cachename, retrieve(URL_FILE + file_path, fpath))

def servemedia(msg):
    '''
    Reply type and link of media. This only generates links for photos.
    '''
    keys = tuple(msg.keys() & MEDIA_TYPES)
    if not keys:
        return ''
    ret = '<%s>' % keys[0]
    if 'photo' not in msg:
        return ret
    servemode = CFG.get('servemedia')
    if servemode:
        fname, code = cachemedia(msg)
        if servemode == 'self':
            ret += ' %s%s' % (CFG['serveurl'], fname)
        elif servemode == 'vim-cn':
            r = requests.post('http://img.vim-cn.com/', files={'name': open(os.path.join(CFG['cachepath'], fname), 'rb')})
            ret += ' ' + r.text
    return ret

def dc_getufname(user, maxlen=100):
    USER_CACHE[user['id']] = (user.get('username'), user.get('first_name'), user.get('last_name'))
    name = user['first_name']
    if 'last_name' in user:
        name += ' ' + user['last_name']
    if len(name) > maxlen:
        name = name[:maxlen] + '…'
    return name

def cmd_t2i(expr, chatid, replyid, msg):
    '''/t2i [on|off] Toggle Telegram to IRC forwarding.'''
    global CFG
    if msg['chat']['id'] == -CFG['groupid']:
        if expr == 'off' or CFG.get('t2i'):
            CFG['t2i'] = False
            sendmsg('Telegram to IRC forwarding disabled.', chatid, replyid)
        elif expr == 'on' or not CFG.get('t2i'):
            CFG['t2i'] = True
            sendmsg('Telegram to IRC forwarding enabled.', chatid, replyid)

def cmd_i2t(expr, chatid, replyid, msg):
    '''/i2t [on|off] Toggle IRC to Telegram forwarding.'''
    global CFG
    if msg['chat']['id'] == -CFG['groupid']:
        if expr == 'off' or CFG.get('i2t'):
            CFG['i2t'] = False
            sendmsg('IRC to Telegram forwarding disabled.', chatid, replyid)
        elif expr == 'on' or not CFG.get('i2t'):
            CFG['i2t'] = True
            sendmsg('IRC to Telegram forwarding enabled.', chatid, replyid)

def cmd_start(expr, chatid, replyid, msg):
    if chatid != -CFG['groupid']:
        sendmsg('This is %s. It can forward messages between %s (Telegram group) and %s (IRC channel).\nSend me /help for help.' % (CFG['botname'], CFG['groupname'], CFG['ircchannel']), chatid, replyid)

def cmd_help(expr, chatid, replyid, msg):
    '''/help Show usage.'''
    if expr:
        if expr in COMMANDS:
            h = COMMANDS[expr].__doc__
            if h:
                sendmsg(h, chatid, replyid)
            else:
                sendmsg('Help is not available for ' + expr, chatid, replyid)
        else:
            sendmsg('Command not found.', chatid, replyid)
    elif chatid == -CFG['groupid']:
        sendmsg('Full help disabled in this group.', chatid, replyid)
    elif chatid > 0:
        sendmsg('This is %s. It can forward messages between %s (Telegram group) and %s (IRC channel).\n' % (CFG['botname'], CFG['groupname'], CFG['ircchannel']) + '\n'.join(cmd.__doc__ for cmd in COMMANDS.values() if cmd.__doc__), chatid, replyid)

# should document usage in docstrings
COMMANDS = collections.OrderedDict((
('start', cmd_start),
('t2i', cmd_t2i),
('i2t', cmd_i2t),
('help', cmd_help)
))

USER_CACHE = LRUCache(20)
MSG_CACHE = LRUCache(10)
CFG = json.load(open('config.json', 'r', encoding='utf-8'))
CFG['offset'] = CFG.get('offset', 0)
URL = 'https://api.telegram.org/bot%s/' % CFG['token']
URL_FILE = 'https://api.telegram.org/file/bot%s/' % CFG['token']

MSG_Q = queue.Queue()

pollthr = threading.Thread(target=getupdates)
pollthr.daemon = True
pollthr.start()

ircconn = None
if 'ircserver' in CFG:
    checkircconn()
    ircthr = threading.Thread(target=getircupd)
    ircthr.daemon = True
    ircthr.start()

# fx233es = fparser.Parser(numtype='decimal')

logging.info('Satellite launched.')

try:
    while 1:
        try:
            processmsg()
        except Exception as ex:
            logging.exception('Failed to process a message.')
            continue
finally:
    json.dump(CFG, open('config.json', 'w'), sort_keys=True, indent=4)
    logging.info('Shut down cleanly.')
