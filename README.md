# Telegram-IRC Relay Bot

A lightweight Telegram-IRC relay bot written in Python. It won't eat messages or memory.

Run: `python3 relay.py [-d]`

`-d` for debug mode.

## Config

The config file must be `config.json`, see the example.
You must disable the privacy mode for your bot.

* __botid__: The number in the token before `:`
* __botname__: Your bot's name
* __groupid__: The Telegram group id to be forwarded. To get it, enable debug mode `-d`, add your bot into the group, say something in the group, and copy the 'message'/'chat'/'id' from the "Msg arrived" line (without `-`). This ID MUST be positive.
* __groupname__: The group's name
* __i2t__: true/false, Enable/disable IRC to Telegram forwarding
* __ircbanre__: Ban users that match the regex. Don't be empty, use "^$" to disable.
* __ircbotid__: The ID of the bot which forwards messages. Usually it should be the same as `botid`, except that there is another bot responsible for this.
* __ircbotname__: The name (in Telegram) of the forwarding bot. Usually it should be the same as `botname`
* __ircchannel__: The IRC channel to forward
* __ircnick__: The bot's nickname in IRC
* __ircport__, __ircserver__, __ircssl__: How to connect to the IRC server
* __offset__: Use 0 for the first time, don't manually change it after
* __t2i__: true/false, Enable/disable Telegram to IRC forwarding
* __token__: Your bot's token

## See also

* [orz-telegram-bot](https://github.com/wfjsw/orz-telegram-bot), a node.js version.
* [tg-chatdig](https://github.com/gumblex/tg-chatdig), on which this bot is based.
