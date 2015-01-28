#!/usr/bin/env python2.7

import redis
import time
import ConfigParser
from math import ceil, sqrt
import mosquitto
import sys

def add_reader(ip_address, reader, description, x, y):
    print '{"reader":%s,"reader_name":"%s","description":"%s","x":%s,"y":%s}' % (ip_address, reader, description, x, y)

def add_ref_tag(tag_id, tag, description, x, y):
    print '{"tag_id":%s,"tag_name":"%s","description":"%s","x":%s,"y":%s}' % (tag_id, tag, description, x, y)

class defaults(object):
    RFBPROTO_BEACONRTRACKER=24          # BEACONTRACKER protocol
    RFBPROTO_PROXREPORT_EXT=70          # PROXREPORT_EXT protocol
    PROXAGGREGATION_TIME = 20           # Aggregation time for proximity readings between tags
    TAG_TIMEOUT_SECONDS=90              # Move tags to unsighted_tags_list if not seen for this long
    REMOVE_UNSIGHTED_TAGS_SECONDS=90    # Remove tags from unsighted list if in this list for this long
    READER_TIMEOUT_SECONDS=(60*15)      # Move reader to unsighted_reader_list if not seen for this lon
    PACKET_STATISTICS_READER = 15       # Time between statistics calulations
    TAGSIGHTING_BUTTON_TIME_SECONDS=5   # Keep flag set in Redis for this long, then expire the tag using Redis expire
    TAGSIGHTINGFLAG_BUTTON_PRESS=0x02   # Mask to detect tag button press
    TAG_MASS = 1.0
    MIN_AGGREGATION_SECONDS = 5
    MAX_AGGREGATION_SECONDS = 16        # Time over which to aggregate sightings
    PAKET_STATISTICS_WINDOW = 10
    REDIS_HOST = "winter.ceit.uq.edu.au"
    REDIS_PORT = 6379
    MQTT_HOST = "winter.ceit.uq.edu.au"
    MQTT_PORT = 1883
    TEA_CRYPTO_KEY = [0x00112233, 0x44556677, 0x8899aabb, 0xccddeeff]    # default key for Openbeacon
    MQTT_READER_TOPIC = "/openbeacon/LIB/fastRaw/reader/+"        # "/openbeacon/LIB/raw/reader/+"
    MQTT_LOG_TOPIC ="/openbeacon/log"

    def __init__(self, db_obj, filename="openbeacon-tracker.ini", setup_readers=False):
        self.d = db_obj
        self.config = ConfigParser.ConfigParser()
        if not self.config.read(filename):
            sys.stderr.write("openbeacon: cannot open configuration file (%s)\n" % filename)
            sys.exit(1)
        if setup_readers == True:
            self.get_readers()
        self.get_defaults()

    def get_readers(self):
        reader_list = self.config.options("reader_list")
        for reader in reader_list:
            if self.config.getboolean("reader_list", reader):
                ip_address = self.config.get(reader, "ip_address")
                description = self.config.get(reader, "description")[1:-1]
                x = self.config.getint(reader, "x")
                y = self.config.getint(reader, "y")
                self.d.add_reader(ip_address, reader, description, x, y)

    def get_defaults(self):
        if self.config.has_option("defaults", "PROXAGGREGATION_TIME"):
            self.PROXAGGREGATION_TIME = self.config.getint("defaults", "PROXAGGREGATION_TIME")
        if self.config.has_option("defaults", "TAG_UNISGHTED_SECONDS"):
            self.TAG_TIMEOUT_SECONDS = self.config.getint("defaults", "TAG_UNISGHTED_SECONDS")
        if self.config.has_option("defaults", "REMOVE_UNSIGHTED_TAGS_SECONDS"):
            self.REMOVE_UNSIGHTED_TAGS_SECONDS = self.config.getint("defaults", "REMOVE_UNSIGHTED_TAGS_SECONDS")
        if self.config.has_option("defaults", "PACKET_STATISTICS_READER"):
            self.PACKET_STATISTICS_READER = self.config.getint("defaults", "PACKET_STATISTICS_READER")
        if self.config.has_option("defaults", "TAGSIGHTING_BUTTON_TIME_SECONDS"):
            self.TAGSIGHTING_BUTTON_TIME_SECONDS = self.config.getint("defaults", "TAGSIGHTING_BUTTON_TIME_SECONDS")
        if self.config.has_option("defaults", "TAGSIGHTING_BUTTON_TIME_SECONDS"):
            self.TAGSIGHTING_BUTTON_TIME_SECONDS = self.config.getint("defaults", "TAGSIGHTING_BUTTON_TIME_SECONDS")
        if self.config.has_option("mqtt", "HOST"):
            self.MQTT_HOST = self.config.get("mqtt", "HOST")
        if self.config.has_option("mqtt","PORT"):
            self.MQTT_PORT = self.config.getint("mqtt","PORT")
        if self.config.has_option("mqtt", "MQTT_READER_TOPIC"):
            self.MQTT_READER_TOPIC = self.config.get("mqtt", "MQTT_READER_TOPIC")
        if self.config.has_option("defaults", "MQTT_LOG_TOPIC"):
            self.MQTT_LOG_TOPIC = self.config.get("mqtt", "MQTT_LOG_TOPIC")
        if self.config.has_option("redis", "HOST"):
            self.REDIS_HOST = self.config.get("redis", "HOST")
        if self.config.has_option("redis", "PORT"):
            self.REDIS_PORT = self.config.getint("redis", "PORT")

class Keys():
    statistics = "statistics"
    readerList = "reader_list"
    readerTagList = "reader_tag_list"
    proximityList = "proximity_list"
    tagList = "tag_list"

def aggregate_power_level(item_key):
    ''' This is a type of proxy for RSSI. For each pair of item tags, we need to determine a value for 
    the 'strength' of the closeness. We will use a weighted sum to do this.  Thus, the calculation 
    will be the number of items received at q particular strenth 'n' (i.e., the length of the 
    list) multiplied by the number of strength levels -1.
    Specifically, we want:
      "item"::<id1>:<id2>:strength:0 * 4 + "item":<id1>:<id2>:strength:1 * 3 + "item":<id1>:<id2>:strength:2 * 2 +
       <id1>:<id2>:strength:3 * 1
    Note that this weights the weakest signal count the most, which would be the case for the closer the
    badgesare placed. '''
    value = 0
    for s in range(0, 4):
        key_strength = (item_key + ":strength:%d") % s
        num_items = r.zcard(key_strength)
        value = value + num_items * (4 - s)
    return value

def str_tag(key_tag):
    tag_id = r.hget(key_tag, "tag_id")
    key_button_tag = "tag:%s:button" % tag_id
    button = ',"button": true' if r.exists(key_button_tag) else ''
    ''' Return a string describing a single tag.'''
    return '  {"id":%s %s, ' % (tag_id, button)

def display_reader_RSSI(key_tag, now):
    ''' For this tag, scan through the readers and record the RSSI for each. '''
    n = r.zcard(Keys.readerList);
    if n > 0:
        result_list = []
        result_list.append('"reader_rssi": [\n')
        reader_list = r.zrange(Keys.readerList, 0, n-1)
#        print '  reader_list', reader_list
        items_remaining = len(reader_list)
        for item in reader_list:
            items_remaining -= 1
            reader_entry = '      {"reader":"%s", "rssi":%d}' % (item, aggregate_power_level("reader:%s:%s" % (item, key_tag)) )
            result_list.append('%s%s' % (reader_entry, ',\n' if items_remaining > 0 else '\n      ]\n' ))
        str = ''.join(result_list)
        return str
    else:
        return ' '

def display_tag_list(now):
    ''' We want to determine four things about each tag: which is the nearest reader, 
    where in the space is the tag estimated to be (not yet here), and if the button has
    been pressed. '''
    # Check if the tag has not been sighted for RESET_TAG_POSITION_SECONDS.  If unsighted, then ignore the tag.
    n = r.zcard(Keys.tagList)
    if n > 0:
        # A string to save all these records in for printing or an MQTT message
        result_list = []
        tokens = r.zrange(Keys.tagList, 0, n-1)
        items_remaining = len(tokens)
        for key_tag in tokens:
            s = display_reader_RSSI(key_tag, now)
            items_remaining -= 1
            result_list.append('  %s %s%s' % (str_tag(key_tag), s, '    },\n' if items_remaining > 0 else '    }\n'))
#        print 'display_tag_list:' + ''.join(result_list)
        return ''.join(result_list)
    else:
        print 'display_tag_list: <empty'
        return ' '

def str_reader_item(item):
    reader = "reader:%s" % item
    id = r.hget(reader, "reader_id")
    name = r.hget(reader, "reader_name")
    desc = r.hget(reader, "description")
    ''' Return a string describing a single reader.'''
    return '{"id":"%s","name":"%s","description":%s}' % (id, name, desc)

def display_reader_list():
    n = r.zcard(Keys.readerList);
    if n > 0:
        result_list = []
        reader_list = r.zrange(Keys.readerList, 0, n-1)
#        print '  reader_list', reader_list
        items_remaining = len(reader_list)
        for item in reader_list:
            items_remaining -= 1
            result_list.append('    %s%s' % (str_reader_item(item), ',\n' if items_remaining > 0 else '\n'))
        return ''.join(result_list)
    else:
        return ' '

def str_proximity_item(item):
    ''' Return a string describing a single promity reading.'''
    tag1 = r.hget(item, "tag1_id")
    tag2 = r.hget(item, "tag2_id")
    return '{"tag":[%s,%s],"power":%d}' % (tag1, tag2, aggregate_power_level(item))

def display_proximity_list():
    # A string to save all these records in for printing or an MQTT message
    n = r.zcard(Keys.proximityList)
    if n > 0:
        result_list = []
        item_list = r.zrange(Keys.proximityList, 0, n-1)
        items_remaining = len(item_list)
        for item in item_list:
            items_remaining -= 1
            result_list.append('    %s%s' % (str_proximity_item(item), ',\n' if items_remaining > 0 else '\n'))
#        print 'display_proximity_list:' + ''.join(result_list)
        return ''.join(result_list)
    else:
        return ' '

# Main program loop
mqttc = mosquitto.Mosquitto()
mqttc.connect("winter.ceit.uq.edu.au", 1883, 60)

r = redis.Redis(host="winter.ceit.uq.edu.au", db=0)

rc = 0

try:
    while rc == 0:
#        current_time = int(time.time())
        current_time = time.time()

#        calculate_tag_position(current_time)

        s = '{' + ('"time":%d' % current_time) + ',\n' + \
         ' "tag":[\n' + display_tag_list(current_time) + '  ],\n' + \
         ' "reader":[\n' + display_reader_list() + '  ],\n' + \
         ' "edge":[\n' + display_proximity_list() + '  ]\n' + \
         '}\n'

#        print s
        
        mqttc.publish("/obUQ/LIB/item/info", s, 0)
        mqttc.loop(0)
        
        time.sleep(0.2)
except KeyboardInterrupt:
    print 'Program closed via keyboard'

mqttc.close()
sys.exit(0)

    
