#!/usr/bin/env python2.7

import redis
import time

class defaults():
    RFBPROTO_BEACONRTRACKER=24          # BEACONTRACKER protocol
    RFBPROTO_PROXREPORT_EXT=70          # PROXREPORT_EXT protocol
    PROXAGGREGATION_TIME = 20           # Aggregation time for proximity readings between tags
    TAG_TIMEOUT_SECONDS=90              # Move tags to unsighted_tags_list if not seen for this long
    REMOVE_UNSIGHTED_TAGS_SECONDS=90    # Remove tags from unsighted list if in this list for this long
    READER_TIMEOUT_SECONDS=(60*15)      # Move reader to unsighted_reader_list if not seen for this lon
    PACKET_STATISTICS_READER = 15       # Time between statistics calulations
    TAGSIGHTING_BUTTON_TIME_SECONDS=5   # Keep flag set in Redis for this long, then expire the tag using Redis expire
    TAGSIGHTINGFLAG_BUTTON_PRESS=0x02   # Mask to detect tag button press
    MIN_AGGREGATION_SECONDS = 5
    MAX_AGGREGATION_SECONDS = 16        # Time over which to aggregate sightings
    REDIS_HOST = "winter.ceit.uq.edu.au"
    REDIS_PORT = 6379
    MQTT_HOST = "winter.ceit.uq.edu.au"
    MQTT_PORT = 1883
    TEA_CRYPTO_KEY = [0x00112233, 0x44556677, 0x8899aabb, 0xccddeeff]    # default key for Openbeacon
    MQTT_READER_TOPIC = "/openbeacon/LIB/fastRaw/reader/+"        # "/openbeacon/LIB/raw/reader/+"
    MQTT_LOG_TOPIC ="/openbeacon/log"

class Keys():
    statistics = "statistics"
    readerList = "reader_list"
    unSightedReaderList = "unsighted_reader_list"
    readerTagList = "reader_tag_list"
    proximityList = "proximity_list"
    tagList = "tag_list"
    unSightedTagsList = "unsighted_tags_list"

r = redis.Redis(host=defaults.REDIS_HOST, db=0)

#
# Determine unsighted items
#

def find_unsighted_readers(now):
    ''' Scan the ordered set 'readerList' and collect all the expired items. '''
    num_items = r.zcount(Keys.readerList, 0, now)
    if num_items > 0:
        items = r.zrangebyscore(Keys.readerList, 0, now)
        for item in items:
            r.zadd(Keys.unSightedReaderList, item, now)
        r.zrem(Keys.readerList, *items)

def find_unsighted_tags(now):
    ''' Scan the ordered set 'tagList' and collect all the expired items. Also, remove all the recorded sightings
        under the various 'power' levels and 'reader_id sightsings, as they are of no use now. '''
    num_items = r.zcount(Keys.tagList, 0, now)
    if num_items > 0:
        items = r.zrangebyscore(Keys.tagList, 0, now)
        for item in items:
            r.delete(item)
            r.zadd(Keys.unSightedTagsList, item, now)
#            print "  ## unsighted tags: removed %s"%item
        string_list = ["tag:%s:button" % id for id in items]
#        print string_list
        r.delete(*string_list)
        r.zrem(Keys.tagList, *items)

def find_unsighted_reader_tag_pairs(now):
    ''' 1. For reader-tag pairs, expire the old sighting entries for each signal strength.
        2. remove expired reader-tag sightings.'''
    # FIRST: for each entry in the reader-tag list, scan each of the four sets
    # of sightings at a particular signal strength and remove expired entries
    num_items = r.zcount(Keys.readerTagList, 0, now)
    if num_items > 0:
        items = r.zrangebyscore(Keys.readerTagList, 0, now)
        # delete all the sightings at each 'strength' level for each item
        for item in items:
            r.zremrangebyscore("%s:strength:0" % item, 0, now)
            r.zremrangebyscore("%s:strength:1" % item, 0, now)
            r.zremrangebyscore("%s:strength:2" % item, 0, now)
            r.zremrangebyscore("%s:strength:3" % item, 0, now)
            # in the 'reader_tag_list'.
            r.zrem(Keys.readerTagList, item)
            # THIRD: remove the key.
#            print 'removed %s from readerTagList'%item
            r.delete(item)

def find_unsighted_proximity_pairs(now):
    ''' Scan the ordered set 'proximityList' and collect all the expired items. '''
    num_items = r.zcount(Keys.proximityList, 0, now)
    if num_items > 0:
        expire_at = now # at a later date, we may set a time betond which we remove the entry
        items = r.zrangebyscore(Keys.proximityList, 0, now)
        item_list = []
        # delete all the sightings at each 'strength' level for each item
        for item in items:
            r.zremrangebyscore("%s:strength:0" % item, 0, now)
            r.zremrangebyscore("%s:strength:1" % item, 0, now)
            r.zremrangebyscore("%s:strength:2" % item, 0, now)
            r.zremrangebyscore("%s:strength:3" % item, 0, now)
            r.zrem(Keys.proximityList, item)
            item_list = ["%s:strength:%d" % (item, level) for level in xrange(0, 4)]
            r.delete(*item_list)
            r.delete(item)

#
# Now, for all active readers/tags/reftags/proximity list items remove all expired sightings: reader:tag, reference_tag:tag and tag:tag
#

def delete_expired_sightings(key, now):
    ''' Empty a sorted set list.
    Eventually, we need to delete each of the sighting keys, to recover memory. For now,
    just return the items that need to be expired.  '''
    n = r.zcard(key)
    if n > 0:
        items = r.zrange(key, 0, n-1)
#        print 'delete_expired_sightings', items
        for item in items:
            r.zremrangebyscore("%s:strength:0" % item, 0, now)
            r.zremrangebyscore("%s:strength:1" % item, 0, now)
            r.zremrangebyscore("%s:strength:2" % item, 0, now)
            r.zremrangebyscore("%s:strength:3" % item, 0, now)

########
#
# Main loop of the program
#

while True:
    current_time = int(time.time())
    
    find_unsighted_readers(current_time)
    find_unsighted_tags(current_time)

    find_unsighted_reader_tag_pairs(current_time)
    find_unsighted_proximity_pairs(current_time)

    delete_expired_sightings(Keys.readerTagList, current_time)
    delete_expired_sightings(Keys.proximityList, current_time)

    time.sleep(0.2)
