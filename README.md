# openbeacon-tracker
A set of processes to convert Openbean tag readings into room cordinates (eventually).

This package consists of three basic processes to produce averaged tag data:

+ **openbeacon\_mqtt\_2\_redis.py** collects data from the Openbeacon readers and stores them in data structures in Redis.
+ **redis-walker.py** walks through the data structures and produces periodic reports on sightings for each tag and each reader.
+ **ob\_redis\_cleanup.py** runs periodically removing expired sightings from the data structures.

The processes **openbeacon\_mqtt\_2_redis.py** and **ob\_redis\_cleanup.py** run on as daemons. **redis-walker.py** can be run by the user whenever data needs to be collected, or it can be left to run as a daemon and users subscribe to its output topic as needed.

Communication between the Openbeacon readers and **openbeacon\_mqtt\_2\_redis.py** is via publish-subscribe using MQTT. 

**redis-walker.py** extracts data from the Redis database and publishes the results to a topic also using MQTT. Any number of localisation algoritms can attach to this topic for their input data.

Configuration information describing readers and tags is held in the file **openbeacon-tracker.ini** and can be accessed by any of the processes.

Separate processes are used rather than multi-threading a single process to simplify the development of the code while we experiment with different approaches.  I expect that this particular configuration will remain until after we implement the localisation algorithm. NOTE: In this project I experimented with using Redis for the first time; some of the redis code is VERY flakey 

I have a couple of ideas to explore for localisation. First we will implement the LANDMARC approach using stationary tags to provide RSSI reference data.  Location information can then be published to a new topic via MQTT. We then want to look at adding a Kalman filter to track a badge using the data from this new topic.  We then want to look at using a particle filter to track multiple badges.

Mark Schulz
Jan 2015
