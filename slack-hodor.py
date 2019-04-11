#! /usr/bin/python3

import time
from time import sleep
import calendar
from datetime import datetime
import pytz
import sys
from slackclient import SlackClient
import os


def triple_quote(msg):
    return "```" + msg + "```"


def kst_to_epoch(kst_time):
    try:
        local_tz = pytz.timezone("Asia/Seoul")
        ts = datetime.strptime(kst_time, "%Y-%m-%d %H:%M:%S")
        ts = local_tz.localize(ts)
        ts = str(ts.astimezone(pytz.utc))[0:19]
        ts = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return int(calendar.timegm(ts.utctimetuple()))
    except Exception:
        return 0
    return 0


def epoch_to_kst(epoch_ts, fmt):
    try:
        tz = pytz.timezone("Asia/Seoul")
        dt = datetime.fromtimestamp(epoch_ts, tz)
        return dt.strftime(fmt)
    except Exception:
        return ""
    return ""


def distance_bw_periods(p1, p2):
    s1, e1 = p1
    s2, e2 = p2
    # self | | other
    if e1 < s2:
        return s2 - e1
    # other | | self
    elif e2 < s1:
        return s1 - e2
    # overlapped
    else:
        return 0


class Reservation:
    def __init__(self, room, start, end, reserved_by, reserved_at):
        self._room = room
        self._start = start
        self._end = end
        self._reserved_by = reserved_by
        self._reserved_at = reserved_at
        self._canceled_by = ""
        self._canceled_at = 0

    def __str__(self):
        room = self._room
        date_kst = epoch_to_kst(self._start, "%Y-%m-%d")
        start_kst = epoch_to_kst(self._start, "%H:%M")
        end_kst = epoch_to_kst(self._end + 1, "%H:%M")
        if self._canceled_at == 0:
            reserved_by = "<@%s>" % self._reserved_by
            reserved_at = epoch_to_kst(self._reserved_at, "%Y-%m-%d %H:%M:%S")
            return "[%s, %s %s ~ %s] reserved by %s at %s" % (
                room,
                date_kst,
                start_kst,
                end_kst,
                reserved_by,
                reserved_at,
            )
        else:
            canceled_by = "<@%s>" % self._canceled_by
            canceled_at = epoch_to_kst(self._canceled_at, "%Y-%m-%d %H:%M:%S")
            return "[%s, %s %s ~ %s] canceled by %s at %s" % (
                room,
                date_kst,
                start_kst,
                end_kst,
                canceled_by,
                canceled_at,
            )

    def time_slot(self):
        return [self._start, self._end]

    def reserved_by(self):
        return self._reserved_by

    def cancel(self, canceled_by, canceled_at):
        self._canceled_by = canceled_by
        self._canceled_at = canceled_at


def make_help(bot_name):
    help_msg = "```"
    help_msg += "@%s help\n" % bot_name
    help_msg += "@%s rooms\n" % bot_name
    help_msg += "@%s reserves\n" % bot_name
    help_msg += "@%s add <room>\n" % bot_name
    help_msg += "@%s remove <room>\n" % bot_name
    help_msg += "@%s reserve <room> <yyyy-mm-dd> <HH:MM> <HH:MM>\n" % bot_name
    help_msg += "@%s cancel <room> <yyyy-mm-dd> <HH:MM>\n" % bot_name
    help_msg += "```"
    return help_msg


def public_channels(sc):
    try:
        res = sc.api_call(
            "conversations.list", exclude_archived="true", types="public_channel"
        )
        if res["ok"]:
            channels = {x["name"]: x["id"] for x in res["channels"]}
            reversed_channels = {x["id"]: x["name"] for x in res["channels"]}
            return channels, reversed_channels
    except Exception:
        pass
    return {}, {}


def private_channels(sc):
    try:
        res = sc.api_call(
            "conversations.list", exclude_archived="true", types="private_channel"
        )
        if res["ok"]:
            channels = {x["name"]: x["id"] for x in res["channels"]}
            reversed_channels = {x["id"]: x["name"] for x in res["channels"]}
            return channels, reversed_channels
    except Exception:
        pass
    return {}


def all_channels(sc):
    direct_channels, reversed_channels = {}, {}
    private_direct, private_reversed = private_channels(sc)
    public_direct, public_reversed = public_channels(sc)
    direct_channels.update(private_direct)
    direct_channels.update(public_direct)
    reversed_channels.update(private_reversed)
    reversed_channels.update(public_reversed)
    return direct_channels, reversed_channels


def channel_members(sc, channel_id):
    try:
        res = sc.api_call("conversations.members", channel="%s" % channel_id)
        return res["members"] if res["ok"] else []
    except Exception:
        pass
    return []


class RoomKeeper:
    def __init__(self, bot_id, bot_name, bot_token, home_name):
        self._bot_id = bot_id
        self._bot_name = bot_name
        self._bot_token = bot_token
        self._bot_tag = "<@%s>" % self._bot_id
        self._home_name = home_name
        self._home_id = ""
        self._boss_ids = []
        self._sc = SlackClient(self._bot_token)
        self._reserved_map = {}
        self._help_msg = make_help(bot_name)

    def __is_valid(self):
        return self.__validate_home()

    def __validate_home(self):
        self.__refresh_channels()
        if self._home_name in self._direct_channels:
            self._home_id = self._direct_channels[self._home_name]
            self._boss_ids = channel_members(self._sc, self._home_id)
            return self._bot_id in self._boss_ids
        return False

    def __refresh_bosses(self):
        self._boss_ids = channel_members(self._sc, self._home_id)

    def __is_my_boss(self, user_id):
        self.__refresh_bosses()
        return user_id in self._boss_ids

    def __refresh_channels(self):
        self._direct_channels, self._reversed_channels = all_channels(self._sc)

    def __is_member_of(self, channel_name):
        if channel_name in self._direct_channels:
            channel_id = self._direct_channels[channel_name]
            members = channel_members(self._sc, channel_id)
            return self._bot_id in members
        return False

    def __validate_cmd(self, cmdline):
        tokens = cmdline.split()
        if len(tokens) == 1 and tokens[0] == "help":
            return tokens[0], (None)
        elif len(tokens) == 1 and tokens[0] == "rooms":
            return tokens[0], (None)
        elif len(tokens) == 1 and tokens[0] == "reserves":
            return tokens[0], (None)
        elif len(tokens) == 2 and tokens[0] == "add":
            return tokens[0], (tokens[1])
        elif len(tokens) == 2 and tokens[0] == "remove":
            return tokens[0], (tokens[1])
        elif len(tokens) == 5 and tokens[0] == "reserve":
            return tokens[0], (tokens[1], tokens[2], tokens[3], tokens[4])
        elif len(tokens) == 4 and tokens[0] == "cancel":
            return tokens[0], (tokens[1], tokens[2], tokens[3])
        return "none", None

    def __post_msg(self, msg, channels):
        for chan in channels:
            try:
                self._sc.api_call(
                    "chat.postMessage", as_user="true", channel=chan, text=msg
                )
            except Exception:
                print("Failed to post translated msg to %s" % chan)

    def __rooms(self):
        rooms = ""
        for room in self._reserved_map:
            rooms += "%s\n" % room
        return rooms

    def __reserves(self):
        reserves = ""
        for room in self._reserved_map:
            for re in self._reserved_map[room]:
                reserves += "%s\n" % re
        return reserves

    def cmd_none(self, user, args):
        msg = "Opps!!! Command not found. Supported commands:\n"
        msg += self._help_msg
        return msg

    def cmd_help(self, user, args):
        msg = "Hi there, I am here to help you reserve meeting rooms\n"
        msg += "Supported commands:\n"
        msg += self._help_msg
        return msg

    def cmd_rooms(self, user, args):
        if not self._reserved_map:
            msg = "No meeting rooms added yet."
        else:
            msg = "Meeting rooms:\n"
            msg += triple_quote(self.__rooms())
        return msg

    def cmd_reserves(self, user, args):
        reserves = self.__reserves()
        if not reserves:
            return "No reserves made yet."
        else:
            msg = "Reserves:\n"
            msg += triple_quote(reserves)
            return msg

    def cmd_add(self, user, args):
        # refresh list of bosses
        self.__refresh_bosses()

        # if the sender is not the bot's boss -> warn him
        if not self.__is_my_boss(user):
            msg = "You don't have permission to add new meeting rooms.\n"
            msg += "Please join %s first!" % self._home_name
            return msg

        room = args

        # room existed
        if room in self._reserved_map:
            msg = "%s already existed. Please double-check!" % room
            return msg

        # add to reserved_map
        self._reserved_map[room] = []

        msg = "Successfully added. Congrats!!!\n"
        msg += "Meeting rooms:\n"
        msg += triple_quote(self.__rooms())

        return msg

    def cmd_remove(self, user, args):
        # refresh list of bosses
        self.__refresh_bosses()

        # if the sender is not the bot's boss -> warn him
        if not self.__is_my_boss(user):
            msg = "You don't have permission to remove meeting rooms.\n"
            msg += "Please join %s first!" % self._home_name
            return msg

        room = args

        # room NOT existed
        if room not in self._reserved_map:
            msg = "%s NOT existed. Please double-check!" % room
            return msg

        # remove from reserved_map
        del self._reserved_map[room]

        msg = "Successfully removed. Congrats!!!\n"
        msg += "Meeting rooms:\n"
        msg += triple_quote(self.__rooms())

        return msg

    def cmd_reserve(self, user, args):
        room, date, start, end = args
        now = int(time.time())

        # room NOT existed
        if room not in self._reserved_map:
            msg = "%s NOT existed. Please double-check!" % room
            return msg

        # start ~ 14:00
        # end ~ 15:00
        start_kst = "%s %s:00" % (date, start)
        end_kst = "%s %s:00" % (date, end)
        start_epoch = kst_to_epoch(start_kst)
        end_epoch = kst_to_epoch(end_kst)

        if start_epoch == 0 or end_epoch == 0 or start_epoch >= end_epoch:
            msg = "Invalid date, start / end. Please double-check!"
            return msg

        if start_epoch - now < 5 * 60:  # too late ?
            msg = "Too late for reservation. Please reserve at least 5 mins in advance!"
            return msg

        if start_epoch - now > 7 * 86400:  # too early ?
            msg = "Too early for reservation. Please reserve at most 7 days in future!"
            return msg

        if end_epoch - start_epoch < 5 * 60:  # too short time slot
            msg = "Too short time slot. Please reserve a time slot of at least 5 mins!"
            return msg

        if end_epoch - start_epoch > 12 * 3600:  # too long time slot
            msg = "Too long time slot. Please reserve a time slot of at most 12 hours!"
            return msg

        end_epoch -= 1
        desired_slot = [start_epoch, end_epoch]

        for re in self._reserved_map[room]:
            existed_slot = re.time_slot()
            if distance_bw_periods(desired_slot, existed_slot) == 0:
                msg = "The time slot has been occupied. Please choose another room / time slot!\n"
                msg += triple_quote("%s" % re)
                return msg

        # reserve
        re = Reservation(room, start_epoch, end_epoch, user, now)
        self._reserved_map[room].append(re)
        self._reserved_map[room].sort(key=lambda x: x._start)

        msg = "Successfully reserved. Congrats!!!\n"
        msg += triple_quote("%s" % re)

        return msg

    def cmd_cancel(self, user, args):
        room, date, start = args
        now = int(time.time())

        # room NOT existed
        if room not in self._reserved_map:
            msg = "%s NOT existed. Please double-check!" % room
            return msg

        # start ~ 14:00
        # end ~ 15:00
        start_kst = "%s %s:00" % (date, start)
        start_epoch = kst_to_epoch(start_kst)

        if start_epoch == 0:
            msg = "Invalid date, start. Please double-check!"
            return msg

        idx = -1
        found_idx = -1
        for re in self._reserved_map[room]:
            idx += 1
            slot = re.time_slot()
            if start_epoch == slot[0]:
                found_idx = idx
                break

        if found_idx == -1:
            msg = "Target room & time slot NOT existed. Please double-check!"
            return msg

        re = self._reserved_map[room][found_idx]

        # refresh list of bosses
        self.__refresh_bosses()

        # neither owner nor boss
        if re.reserved_by() != user and not self.__is_my_boss(user):
            msg = "You don't have permission to cancel the reservation."
            msg += triple_quote("%s" % re)
            return msg

        re.cancel(user, now)
        del self._reserved_map[room][found_idx]
        self._reserved_map[room].sort(key=lambda x: x._start)

        msg = "Successfully canceled. Congrats!!!\n"
        msg += triple_quote("%s" % re)

        return msg

    def cmd_clean(self):
        now = int(time.time())
        for room in self._reserved_map:
            tb_removed = []
            for i in range(len(self._reserved_map[room])):
                re = self._reserved_map[room][i]
                if re._end < now:
                    tb_removed.append(i)
            for j in sorted(tb_removed, reverse=True):
                del self._reserved_map[room][j]

    def __do_settings(self, user, cmdline):
        # take this chance to clean stale reserves
        self.cmd_clean()

        # do settings
        cmd, args = self.__validate_cmd(cmdline)
        functor = getattr(self, "cmd_%s" % cmd)
        msg = functor(user, args)
        return msg

    def __connect(self):
        print("Connecting to slack")
        return self._sc.rtm_connect()

    def run_forever(self):
        # invalid ?
        if not self.__is_valid():
            print("Invalid home channel: %s" % self._home_name)
            time.sleep(3.0)
            return

        # connect to slack
        if not self.__connect():
            print("Couldn't connect to slack")
            time.sleep(3.0)
            return

        # keep running
        while True:
            reconnect_needed = False

            # listen to channels
            for slack_event in self._sc.rtm_read():
                print(slack_event)
                msg_type = slack_event.get("type")
                # goodbye message -> reconnect
                if msg_type == "goodbye":
                    print("Goodbye. Reconnecting needed")
                    time.sleep(3.0)
                    reconnect_needed = True
                    break
                # things other than message -> skip
                elif msg_type != "message":
                    continue
                # validate channel, msg, sender, subtype
                src_chan_id = slack_event.get("channel")
                org_msg = slack_event.get("text")
                user = slack_event.get("user")
                subtype = slack_event.get("subtype")
                if (
                    not org_msg
                    or not user
                    or user == self._bot_id
                    or subtype == "bot_message"  # posted by a bot
                ):
                    continue

                # the bot is mentioned at the beginning of the message -> cmd
                if org_msg.find(self._bot_tag) == 0:
                    cmdline = org_msg[len(self._bot_tag) :].strip()
                    msg = self.__do_settings(user, cmdline)
                    self.__post_msg(msg, [src_chan_id])
                # normal message
                else:
                    # silently discard
                    pass

                time.sleep(0.1)

            if reconnect_needed:
                break


def grab_env_vars():
    try:
        bot_id = os.environ["BOT_ID"]
        bot_name = os.environ["BOT_NAME"]
        bot_token = os.environ["BOT_TOKEN"]
        home_chan = os.environ["BOT_HOME"]
        return (bot_id, bot_name, bot_token, home_chan)
    except Exception as ex:
        print("Exception: %s" % ex)
    return ("", "", "", "")


if __name__ == """__main__""":
    if len(sys.argv) != 1:
        print("Invalid inputs")
        sys.exit(1)

    # grab env variables
    (bot_id, bot_name, bot_token, home_chan) = grab_env_vars()
    if not bot_id or not bot_name or not bot_token or not home_chan:
        print("Invalid env variables")
        sys.exit(1)

    # the bot
    room_keeper = RoomKeeper(bot_id, bot_name, bot_token, home_chan)

    # serve forever (hopefully)
    while True:
        try:
            room_keeper.run_forever()
        except KeyboardInterrupt:
            print("Exited upon KeyboardInterrupt")
            break
        except Exception as ex:
            print("Exception: %s" % ex)
            time.sleep(1.0)
