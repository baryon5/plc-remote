
from hardware.app import Context
from hardware.keypad import str_to_bools
from plc.core.settings import conf
from plc.core.data import *
from plc.network.messages import *
from .components import *

def val_to_str(val):
    return ("{:02}".format(val) if val < 100 else "FL")

def to_percent(val):
    return round(val / DMX_MAX_SLOT_VALUE * 100)

def from_percent(val):
    return round(val / 100 * DMX_MAX_SLOT_VALUE)

def justify(val, num, zero=False, trim=False):
    trim = False if type(val) == int else trim
    fmt = "{{:{fill}>{num}{trimnum}}}".format(
        fill="0" if zero else " ", num=num,
        trimnum=("."+str(num) if trim else ""))
    return fmt.format(val)

def void(*args, **kwargs):
    pass

KEYPAD_MAP = {
    0:  7,
    1:  8,
    2:  9,
    3:  "Cue",
    4:  4,
    5:  5,
    6:  6,
    7:  "Grp",
    8:  1,
    9:  2,
    10: 3,
    11: " Ch",
    12: "+/-",
    13: 0,
    14: " * ",
    15: "Rec"
}
        
class PLCContext(Context):
    def __init__(self, app):
        self.__manager_initialized = False
        self.app = app

    def set_manager(self, manager):
        if self.__manager_initialized:
            raise RuntimeError("Manager already set")
        else:
            super().__init__(self.app)
            self.manager = manager
            self.__manager_initialized = True

class NumericEntry:

    limit = 999
    
    def handle_number(self, pin):
        self.buf = self.buf * 10 + KEYPAD_MAP[pin]
        if self.buf > self.limit:
            l = 1
            while self.limit % l != self.limit:
                l *= 10
            self.buf = self.buf % l
            if self.buf > self.limit:
                self.buf = self.buf % int(l / 10)
        self.output(keypad, 3, 2, True)
        self.ready = True

    def prep_numeric_entry(self):
        self.capture(keypad, (0,1,2,4,5,6,8,9,10,13), self.handle_number)
        self.capture(keypad, 14, self.handle_enter)
        for i in range(3):
            self.output(keypad, i, 0, str_to_bools("XXX"))
        self.output(keypad, 3, 1, str_to_bools("X "))
        self.ready = False
        self.buf = 0
        
    def block_numeric_entry(self):
        self.capture(keypad, (0,1,2,4,5,6,8,9,10,13,14), void)
        for i in range(3):
            self.output(keypad, i, 0, str_to_bools("   "))
        self.output(keypad, 3, 1, str_to_bools("  "))

    def reset_numeric_entry(self):
        self.release(keypad, (0,1,2,4,5,6,8,9,10,13,14))
        for i in range(3):
            self.outreset(keypad, i, range(0,3), False)
        self.outreset(keypad, 3, range(1,3), False)
        
class BackgroundContext(PLCContext, NumericEntry):
    """Uses display, keypad (1-11, 13, 15), sub faders and GM
    App must have faders, keypad, and display attributes."""

    def set_manager(self, manager):
        super().set_manager(manager)
        self.xtra = SubContext(self.app)
        self.entry_mode = None
        
    def status(self, val):
        self.output(display, 3, 8, "{: >12.12}".format(val))
            
    def enter(self):
        self.status("Loading...")
        self.capture(faders, SUB_FADERS, self.handle_sub)
        self.capture(faders, GM_FADER, self.handle_gm)
        self.output(display, 3, 0, "M")
        self.xtra.enter()
        self.capture(keypad, (3, 7, 11), self.begin_entry)
        self.status("Ready")

    def handle_cue(self, pin):
        self.status("*No Cue Ops*")

    def begin_entry(self, pin):
        self.record_enable = False
        self.at_mode = False
        if self.entry_mode == pin:
            self.entry_mode = None
            self.reset_numeric_entry()
            self.release(keypad, (12, 15))
            self.status("Ready")
            for i in range(4):
                self.outreset(keypad, i, 3, False)
            return False
        self.entry_mode = pin
        for i in range(3):
            self.output(keypad, i, 3, False)
        self.output(keypad, pin // 4, 3, True)
        self.prep_numeric_entry()
        if pin == 11:
            self.limit = conf["dimmers"]
        else:
            self.limit = 999
            self.capture(keypad, 12, self.handle_add_remove)
        self.capture(keypad, 15, self.handle_record)
        self.status(KEYPAD_MAP[pin] + " " * 4)
        
    def handle_number(self, pin):
        super().handle_number(pin)
        self.output(keypad, 3, 3, True)
        self.output(display, 3, 17, justify(self.buf, 3))

    def handle_enter(self, pin):
        if self.at_mode:
            self.app.protocol.send_message(DimmerMessage(
                { self.num - 1: from_percent(self.buf) }))
            self.at_mode = False
            self.status("Ready")
            self.reset_numeric_entry()
            self.outreset(keypad, 3, 3, False)
            for i in range(3):
                self.outreset(keypad, i, 3, False)
            self.release(keypad, (12, 15))
            self.entry_mode = None
        else:
            self.num = self.buf
            self.buf = 0
            self.limit = 99
            self.status(KEYPAD_MAP[self.entry_mode] + " " +
                        justify(self.num, 3, True) + " @   ")
            self.output(keypad, 3, 3, True)
            self.at_mode = True

    def handle_record(self, pin):
        if KEYPAD_MAP[self.entry_mode] == " Ch":
            self.handle_enter(pin)
            self.buf = 100
            self.handle_enter(pin)
        elif self.record_enable:
            if KEYPAD_MAP[self.entry_mode] == "Grp":
                g = self.manager.get_list("groups").new(self.buf)
                for i, j in self.manager.dimmers.items():
                    g[i] = j / DMX_MAX_SLOT_VALUE
                self.app.protocol.send_message(GroupMessage("update",g))
            elif KEYPAD_MAP[self.entry_mode] == "Cue":
                self.status("*No Cue Ops*")
            self.reset_numeric_entry()
            self.release(keypad, (12, 15))
            for i in range(4):
                self.outreset(keypad, i, 3, False)
            self.entry_mode = None
        else:
            self.block_numeric_entry()
            self.release(keypad, 12)
            self.status("Rec " + KEYPAD_MAP[self.entry_mode]
                        + " " + justify(self.buf, 3, True))
            self.record_enable = True

    def handle_add_remove(self, pin):
        """Launch new context"""
        
    def handle_gm(self, *args):
        val = faders.get(11)
        debug("GM:", val)
        self.output(display, 3, 1, val_to_str(val))

    def handle_sub(self, pin):
        if self.xtra.group == pin + 1:
            return False
        val = faders.get(pin)
        self.app.protocol.send_message(
            GroupMessage("level", pin+1, val/100))
        debug("Sub:", pin+1, "at", val)
        
class SubContext(Context, NumericEntry):
    """Uses X fader and keypad and display..."""

    priority = Context.priority + 2 ** 8
    
    def enter(self):
        self.select(0)
        self.capture(faders, X_FADER, self.handle_fade)
        self.prep_numeric_entry()

    def handle_number(self, pin):
        super().handle_number(pin)
        self.output(display, 3, 4, justify(self.buf, 3) + "*")
        
    def handle_enter(self, pin):
        if self.ready:
            self.select(self.buf)
            self.buf = 0
            self.ready = False
            self.output(keypad, 3, 2, False)
        
    def select(self, num):
        self.output(display, 3, 4, justify(num, 3, True) + " ")
        self.group = num

    def handle_fade(self, *args):
        val = faders.get(X_FADER)
        self.app.protocol.send_message(
            GroupMessage("level", self.group, val/100))
        debug("X:",val)

class DimmerContext(Context):

    def show_dimmers(self, dimmers, source=None):
        ch = " ".join([justify(i+1, 3, True, True) for i, j
                       in sorted(dimmers.items())])
        lvl = " ".join([justify(to_percent(i), 3, True) for j, i
                        in sorted(dimmers.items())])
        self.output(display, 0, 0, ch if len(ch) < 21 else ch[:20])
        self.output(display, 1, 0, lvl if len(lvl) < 21 else lvl[:20])
