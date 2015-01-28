#!/usr/local/bin/python2.7

import sys
import struct
import binascii
import json
import mosquitto
import time
import datetime
import redis
import ConfigParser
import xxtea_python as xxtea
import numpy
import logging
# import logging.handlers

# set up logging
LOG_FILENAME='/tmp/ob_mqtt_2_redis.log'

# create logger
logger = logging.getLogger("badge")
logger.setLevel(logging.INFO)

# create console handler and set level to debug
ch = logging.FileHandler(LOG_FILENAME)
ch.setLevel(logging.DEBUG)

#craete a logging format
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# add formatter to clogger
ch.setFormatter(formatter)

# add the handler to the logger
logger.addHandler(ch)

class defaults(object):
    RFBPROTO_BEACONRTRACKER=24          # BEACONTRACKER protocol
    RFBPROTO_PROXREPORT_EXT=70          # PROXREPORT_EXT protocol
    PROXAGGREGATION_TIME = 20           # Aggregation time for proximity readings between tags
    TAG_TIMEOUT_SECONDS=30              # Move tags to unsighted_tags_list if not seen for this long
    READER_TIMEOUT_SECONDS=90      # Move reader to unsighted_reader_list if not seen for this lon
    PACKET_STATISTICS_READER = 15       # Time between statistics calulations
    TAGSIGHTING_BUTTON_TIME_SECONDS=5   # Keep flag set in Redis for this long, then expire the tag using Redis expire
    TAGSIGHTINGFLAG_BUTTON_PRESS=0x02   # Mask to detect tag button press
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

    def __init__(self, db_obj, filename="/usr/local/etc/openbeacon-tracker.ini", setup_readers=False):
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
                print "add reader: %s" % ip_address
                description = self.config.get(reader, "description")[1:-1]
                x = self.config.getint(reader, "x")
                y = self.config.getint(reader, "y")
                self.d.add_reader(ip_address, reader, description, x, y)
    
    def get_defaults(self):
        if self.config.has_option("defaults", "PROXAGGREGATION_TIME"):
            self.PROXAGGREGATION_TIME = self.config.getint("defaults", "PROXAGGREGATION_TIME")
        if self.config.has_option("defaults", "TAG_UNISGHTED_SECONDS"):
            self.TAG_TIMEOUT_SECONDS = self.config.getint("defaults", "TAG_UNISGHTED_SECONDS")
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
    unSightedReaderList = "unsighted_reader_list"
    readerTagList = "reader_tag_list"
    proximityList = "proximity_list"
    tagList = "tag_list"
    unSightedTagsList = "unsighted_tags_list"

class DataStore(object):
    def __init__(self):
        self.r = redis.Redis(host=defaults.REDIS_HOST, db=0)
        self.r.flushdb()    # remove all keys from the current database
        self.r.hmset(Keys.statistics, \
            {
                "crc_error": 0, \
                "crc_ok": 0, \
                "invalid_protocol": 0, \
                "invalid_reader": 0
            })

    def incr_crc_error_stats(self, reader_id):
        self.r.hincrby(Keys.statistics, "crc_error", 1)                # Update the global CRC error count
        self.r.hincrby("reader:" + reader_id, "crc_error", 1)        # Update the CRC error count for this reader

    def incr_crc_ok_stats(self, reader_id):
        self.r.hincrby(Keys.statistics, "crc_ok", 1)                # Update the global CRC  count
        self.r.hincrby("reader:" + reader_id, "crc_", 1)        # Update the CRC  count for this reader

    def add_reader(self, reader_id, reader_name, description, px, py):
        '''Put data about reader_id into the database (static entry).  This data most likely
            comes from the configuration file.'''
        timestamp = int(time.time())
        # Check for duplicate entries
        if not self.r.zrank(Keys.readerList, reader_id) == None:
            sys.stderr.write("Duplicate reader descriptions: %s" % reader_id)
            sys.exit(1)
        # Record that we have an entry
        self.r.zadd(Keys.readerList, reader_id, timestamp + defaults.READER_TIMEOUT_SECONDS)
        # Update our reader info.  Note that we could add any data in the JSON string, and just accept anything
        # From the .ini file.  Think on this for latter uses.
        key_reader = "reader:%s" % reader_id
        self.r.hmset(key_reader, \
                { "reader_id": reader_id, \
                "reader_name": reader_name, \
                "description": description, \
                "last_seen": timestamp, \
                "crc_ok": 0, \
                "crc_error": 0,
                "x": px, \
                "y": py })
    #        print "add_reader(%s)" % reader_name

    def isValidReader(self, sighted_id):
        ''' Check if this id comes from a valid reader in our network. Report invlaid readers. '''
        isa_sighted = self.r.zrank(Keys.readerList, sighted_id) != None
        isa_unsighted = self.r.zrank(Keys.unSightedReaderList, sighted_id) != None
        isa_reader = isa_sighted or isa_unsighted
#        print '%s is %sa valid %sreader' % (sighted_id, '' if isa_reader else 'NOT ', 'sighted ' if isa_sighted else ' unsighted ' if isa_unsighted else '')
        if not isa_reader:
            self.r.hincrby(Keys.statistics, "invalid_reader", 1)
#            mqttc.publish(defaults.MQTT_LOG_TOPIC, "Invalid reader: %s" % reader_id)
            print defaults.MQTT_LOG_TOPIC, "Invalid reader: %s" % sighted_id
        return isa_reader

    def update_reader_sighting(self, timestamp, reader_id):
        ''' Reader Ids are held in the 'reader_list'.  If the reader is not sighted for READER_TIMEOUT_SECONDS,
            it is moved to the unSightedReaderList'. If a reader is sighted, then make sure that its ID is 
            in the active reader list, it is set to expire (via a helper process) in READER_TIMEOUT_SECONDS. '''
        pipe = self.r.pipeline()
        
        pipe.multi()
        # Now the reader IS sighted, remove it from the 'Keys.unSightedReaderList'.
        pipe.zrem(Keys.unSightedReaderList, reader_id)
        # Add this entry into the 'Keys.readerList', or update its score(expire time) if already there
        pipe.zadd(Keys.readerList, reader_id, timestamp + defaults.READER_TIMEOUT_SECONDS)
        # Record the last time this reader was seen.  Might use this to NOT display readers
        # that disappear for at least a predefined time.
        pipe.hset("reader:%s" % reader_id, "last_seen", timestamp)
        pipe.execute()
        return True

    def update_reader_tag_entry(self, timestamp, reader_id, tag_id, tag_strength):
        '''Associate this 'tag_id' with this 'reader_id'. Keep a list of sightings for each
        of the transmit strengths for the last MAX_AGGREGATION_SECONDS for later calculations.  '''
        # Build the initial reader:tag relationship. Keep a list of all
        # reader_id:tag_id relationships in 'readerTagList'.  We will iterate
        # through this list in a helper process to make our periodic observations.
        key_sighting_tag = "reader:%s:tag:%d" % (reader_id, tag_id)
        key_strength = "%s:strength:%d" % (key_sighting_tag, tag_strength)
        if self.r.zrank(Keys.readerTagList, key_sighting_tag) == None:
            self.r.hmset(key_sighting_tag, \
                { "reader_id": reader_id, \
                "tag_id": tag_id, \
                "sighting_count": 0 \
                })
        count = self.r.hincrby(key_sighting_tag, "sighting_count", 1)
        pipe = self.r.pipeline()
        
        pipe.multi()
        # if the 'reader_id:tag_id' relationship is not in the 'readerTagList', then
        # make a new entry.
        # Add this entry into the 'readerTagList', or update its score(expire time) if already there
        pipe.zadd(Keys.readerTagList, key_sighting_tag, timestamp + defaults.MAX_AGGREGATION_SECONDS)
        # Update the packet statistics for this reader
        pipe.hincrby("reader:%s" % reader_id, "crc_ok", 1)
        pipe.hset("reader:%s" % reader_id, "last_seen", timestamp)
        # Add this sighting to appropriate stength list.  NOTE: 'count' is used to create a
        # unique 'id' for each sighting (needed for the sorted set entry).
        pipe.zadd(key_strength, count, timestamp + defaults.MAX_AGGREGATION_SECONDS)
        pipe.execute()

    def update_tag_entry(self, timestamp, reader_id, tag_id, tag_flags, seq_number):
        '''Record the sighting of 'tag_id', and all the info associated with this
        sighting. This also handles the estimated position of the tag, which is periodically
        (and initially) set to the same position as 'reader_id' every RESET_TAG_POSITIONS_SECONDS.
        NOTE: We may need to drop this as it is specific to just one algorithm and does not generalize. '''
        # Set up a new entry for this tag, if this is the first time seen
        key_tag = "tag:%d" % tag_id
        key_button_tag = "tag:%d:button" % tag_id
        if self.r.zrank(Keys.tagList, key_tag) == None:
            # Create new entry
            self.r.hmset(key_tag, \
                { "tag_id": tag_id, \
                "seq_number": seq_number, \
                "last_seen": timestamp, \
                "last_calculated": 0 \
                })
        pipe = self.r.pipeline()
        
        pipe.multi()
        pipe.hset(key_tag, "seq_number", seq_number)  # this is not used in this application
        pipe.hset(key_tag, "last_seen", timestamp)
        pipe.hset(key_tag, "tag_id", tag_id)
        pipe.hset(key_tag, "last_reader_id", reader_id)
        # Add this sighting entry into the 'tag_list', or update its score if already there
        pipe.zadd(Keys.tagList, key_tag, timestamp + defaults.TAG_TIMEOUT_SECONDS)
        # Check if the button has been pressed on the tag.  Have this record hang around for
        # TAGSIGHTING_BUTTON_TIME_SECONDS after the event, then remove the flag.
        if tag_flags & defaults.TAGSIGHTINGFLAG_BUTTON_PRESS:
            print '  button pressed on %d' % tag_id
            pipe.setex(key_button_tag, timestamp, defaults.TAGSIGHTING_BUTTON_TIME_SECONDS)
        pipe.execute()

    def prox_tag_sighting(self, timestamp, tag1, tag2, tag_strength, num_sightings, sequence):
        if tag1 > tag2:
            t = tag1
            tag1 = tag2
            tag2 = t
        key_prox = "prox:%d:%d" % (tag1, tag2)
        key_strength = "%s:strength:%d" % (key_prox, tag_strength)
        if self.r.zrank(Keys.proximityList, key_prox) == None:
            # Add this entry into the 'proximity_list'
            self.r.hset(key_prox, "tag1_id", tag1)
            self.r.hset(key_prox, "tag2_id", tag2)
            self.r.hset(key_prox, "sighting_count", 0)
        # Update its sighting_count
        count = self.r.hincrby(key_prox, "sighting_count", num_sightings)
        pipe = self.r.pipeline()
        
        pipe.multi()
        # Update the 'time to die' for the 'key_prox' by PROXAGGREGATION_TIME
        pipe.zadd(Keys.proximityList, key_prox, timestamp + defaults.PROXAGGREGATION_TIME)
        # Add this sighting to appropriate stength list
        # 'sighting_count' is used to make a unique key, and stop count loss in the same time period.
        # This can happen if tag_count is greater than 1 -- NOTE I haven't seen this in the data YET!!
        pipe.zadd(key_strength, count, timestamp + defaults.PROXAGGREGATION_TIME)
        pipe.execute()

class myMosquitto(object):
    def __init__(self, dataobj):
        self.mqttc = mosquitto.Mosquitto()
        print 'Mosquitto started'
        self.mqttc.on_message = self.on_message
        self.r = dataobj
        # Uncomment to enable debug messages
        #mqttc.on_log = on_log
        self.mqttc.connect(defaults.MQTT_HOST, defaults.MQTT_PORT, 60)
        self.p = Payload(self.mqttc, dataobj)
    
    def mySubscribe(self):
        self.mqttc.subscribe(defaults.MQTT_READER_TOPIC, 0)
        print 'Subscribed to %s' % defaults.MQTT_READER_TOPIC
    
    def close(self):
        self.mqttc.unsubscribe(defaults.MQTT_READER_TOPIC)
        self.mqttc.disconnect()
    
    def loop(self):
        return self.mqttc.loop(0)
    
    def on_message(self, mosq, obj, msg):
        self.p.unpack(msg.topic, msg.payload)
        
class Payload(object):
    def __init__(self, mqttc, dataobj):
        self.mqttc = mqttc
        self.r = dataobj
        xxtea.set_key(*defaults.TEA_CRYPTO_KEY)

    def invalid_crc(self, reader_id):
        print "Invalid CRC from " + reader_id
        self.r.incr_crc_error_stats(reader_id)
        self.mqttc.publish(defaults.MQTT_LOG_TOPIC, "Invalid CRC from %s" % reader_id)

    def chk_packet(self, reader_id, payload):
        # Decode the packet first, using XXTEA encryption (check Wikipedia)
        try:
            data = xxtea.decode(payload)
        except ValueError, e:
            print 'xxtea: %s from reader %s' % (e, reader_id)
            self.mqttc.publish(defaults.MQTT_LOG_TOPIC, 'xxtea: %s from reader %s' % (e, reader_id))
            return None
        #
        # Verify the CRC now
        #.
        if xxtea.crc16(data) != 0:
            self.invalid_crc(reader_id)
            return None
        self.r.incr_crc_ok_stats(reader_id)
        return data

    def doSighting(self, timestamp, reader, id, flags, data):
        (strength, last_oid, pwrup_cnt, reserved, sequence, crc16_val) = struct.unpack("!BHHBLH", data[4:])
        self.r.update_reader_sighting(timestamp, reader)
        self.r.update_tag_entry(timestamp, reader, id, flags, sequence)
        self.r.update_reader_tag_entry(timestamp, reader, id, strength)

    def doProximity(self, timestamp, reader, id, flags, data):
        (sequence, crc16_val) = struct.unpack("!HH", data[12:16])
        self.r.update_reader_sighting(timestamp, reader)
        self.r.update_tag_entry(timestamp, reader, id, flags, sequence)
        self.r.update_reader_tag_entry(timestamp, reader, id, 3)
        for i in xrange(0,4):
            val = struct.unpack("!H", data[4+i*2:6+i*2])[0]
            if val != 0:
                tag2 = val & 0xFFF
                tag_count = (val >> 12) & 0x3
                tag_strength = (val >> 14) & 0x3
                self.r.update_tag_entry(timestamp, reader, tag2, flags, sequence)
                self.r.prox_tag_sighting(timestamp, id, tag2, tag_strength, tag_count, sequence)

    def getReaderId(self, topic):
        index = topic.rfind("/")
        if index == -1:
            reader = topic
        else:
            reader = topic[index+1:]
        return reader

    def unpack(self, topic, payload):
        timestamp = int(time.time())
        reader = self.getReaderId(str(topic))
        if self.r.isValidReader(reader):
            #
            # Only process data that is sent from a valid reader in the network
            #
            data = self.chk_packet(reader, str(payload))
            if data == None:
                return
            (protocol, id, flags) = struct.unpack("!BHB", data[0:4])
            if protocol == defaults.RFBPROTO_BEACONRTRACKER:
                self.doSighting(timestamp, reader, id, flags, data)
            elif protocol == defaults.RFBPROTO_PROXREPORT_EXT:
                self.doProximity(timestamp, reader, id, flags, data)
            else:
                self.r.incr_value(invalidProtocol)
                self.mqttc.publish(defaults.MQTT_LOG_TOPIC, "Unknown protocol: %d on reader %s" % (protocol, reader), qos=0, retain=True)

class App(object):
    def __init__(self):
        d = DataStore()
        c = defaults(db_obj=d, filename="openbeacon-tracker.ini", setup_readers=True)
        self.mqttc = myMosquitto(d)
    
    def run(self):
        rc = 0
        self.mqttc.mySubscribe()
        try:
          while rc == 0:
              rc = self.mqttc.loop()
        except KeyboardInterrupt:
            print 'Program closed via keyboard'
        self.mqttc.close()
        sys.exit(0)

app = App()
app.run()
