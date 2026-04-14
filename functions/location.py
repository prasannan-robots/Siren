import os, folium, csv, requests, numpy as np
from dotenv import load_dotenv
from database import get_database_value
from math import pi
from sklearn.linear_model import LinearRegression
from datetime import datetime
INTERVAL = 3




def update_map(map, latitude, longitude):
    folium.Marker([latitude, longitude]).add_to(map)
    map.save('../geolocation/map.html')


def get_csv_value(row):
    csv_path = "../geolocation/red-light-cameras.csv"
    with open(csv_path, mode='r') as file:
        csv_reader = csv.reader(file)
        return list(csv_reader)[row]


def get_street_name(api_key, lat, long):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={lat},{long}&key={api_key}"

    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if data.get('results'):
            for component in data['results'][0].get('address_components', []):
                if 'route' in component.get('types', []):
                    return component['long_name']
    return None


def get_driving_distance(api_key, target_coordinate, current_coordinate):    
    base_url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": current_coordinate,
        "destination": target_coordinate,
        "mode": "driving",
        "key": api_key
    }
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        data = response.json()
        if data.get('routes'):
            return data['routes'][0]['legs'][0]['distance']['value']  # meters
    return -1


def format_address(current_street):
    if current_street is None:
        return None
    long_form = {
        " N": " North", " E": " East", " S": " South", " W": " West",
        " Rd": " Road", " St": " Street", " Av": " Avenue",
        " Blvd": " Boulevard", " Dr": " Drive" 
    }
    for abbreviation, full in long_form.items(): # abbreviations with spaces
        current_street = current_street.replace(abbreviation + " ", full + " ")
    for abbreviation, full in long_form.items(): # abbreviations without spaces
        if current_street.endswith(abbreviation):
            current_street = current_street[:-len(abbreviation)] + full
    return current_street


def get_red_light_camera(lat, long):  
    red_intersection, red_region = "", ""
    red_lat, red_long = 0.0, 0.0
    distance = -1
    intersection, region = "", ""

    # reverse geocoding api
    load_dotenv(dotenv_path="../.env")
    API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
    ENTRY_COUNT = 408
   
    current_street = format_address(get_street_name(API_KEY, lat, long))
    if(current_street is None): return None
    for row in range(2, ENTRY_COUNT):
        values = get_csv_value(row)
        red_intersection, red_lat, red_long, red_region = values[0], values[1], values[2], values[3]
        if (current_street in red_intersection):
            temp_distance = get_driving_distance(API_KEY, str(red_lat)+","+str(red_long), str(lat)+","+str(long))
            if(distance==-1 and temp_distance<=200):
                distance = temp_distance
                intersection = red_intersection
                region = red_region
            elif(temp_distance<distance):
                distance = temp_distance
                intersection = red_intersection
                region = red_region

    if(intersection and -1<distance<=300): #300m range
        print("Red light camera approaching in", distance, "m at", intersection)
        return intersection + ", " + region, current_street
    else:
        print("No red light cameras near", current_street), current_street
        return None, current_street


def get_time_difference(prev_time, curr_time):
    prev_time_obj = datetime.strptime(prev_time, '%m_%d_%Y_%H_%M_%S')
    curr_time_obj = datetime.strptime(curr_time, '%m_%d_%Y_%H_%M_%S')
    time_difference = abs((curr_time_obj - prev_time_obj).total_seconds())
    return time_difference


def detect_speeding(lat, long, api_key, speed):
    base_url = "https://roads.googleapis.com/v1/speedLimits"
    params = {
        "path": f"{lat},{long}",
        "key": api_key
    }
    speed_limit = 0
    try:
        response = requests.get(base_url, params=params)
        if response.status_code == 200:
            data = response.json()
            if 'speedLimits' in data and len(data['speedLimits']) > 0:
                speed_limit = int(data['speedLimits'][0]['speedLimit'])
                # Convert mph to km/h if units are MPH
                if data['speedLimits'][0].get('units') == 'MPH':
                    speed_limit = int(speed_limit * 1.60934)
                
                if speed_limit:
                    print(f"Speed limit: {speed_limit} km/h")
                    if speed - speed_limit >= 10:  # if going 10 km/h over
                        return speed_limit, True
        else:
            print(f"Speed limit API unavailable (status {response.status_code}), skipping speed check")
    except Exception as e:
        print(f"Speed limit lookup failed: {e}")
    return speed_limit, False
    

def get_speed_info(cur_lat, cur_long, paths_path, curr_time):
    prev_lat, prev_long = 0.0, 0.0
    boolean = False
    with open(paths_path, 'r') as file:
        lines = file.readlines()
        if lines:
            line = lines[-1].strip()
            prev_lat, prev_long = line.strip().split(', ')[0], line.strip().split(', ')[1]
            prev_time = line.split(', ')[2].split('Time: ')[1].strip()
            print(curr_time, 'and', prev_time)
            time_difference = get_time_difference(prev_time, curr_time)
            
            if(0<time_difference <= 60): # prev location = recorded less than a minute ago
                # reverse geocoding api
                load_dotenv(dotenv_path="../.env")
                api_key = os.getenv("GOOGLE_MAPS_API_KEY")
                distance = get_driving_distance(api_key, str(cur_lat)+","+str(cur_long), str(prev_lat)+","+str(prev_long))
                if(distance<0): distance=0
                speed = round(distance/time_difference*3.6) # m/s --> km/h
                print('Speed:', speed, 'km/h')
                speed_limit, boolean = detect_speeding(str(cur_lat), str(cur_long), api_key, speed)
                return round(speed, 2), speed_limit, boolean 
    return 0.0, 0.0, boolean


def get_coordinates(gps_reader, map):
    """Get coordinates from GPSReader (NEO-6M module)."""
    latitude, longitude = gps_reader.get_position()
    if latitude is not None and longitude is not None:
        update_map(map, latitude, longitude)
        return latitude, longitude
    return None, None


def turn_angle(v1, v2):
    angle = np.arctan2(v2[1], v2[0]) - np.arctan2(v1[1], v1[0])
    angle_deg = np.degrees(angle)
    if angle_deg < 0: angle_deg += 360
    return angle_deg


def detect_turn_direction(latitudes, longitudes, curr_lat, curr_long):
    # Linear regression for line of best fit
    latitudes_reshaped = latitudes.reshape(-1, 1)
    reg = LinearRegression()
    reg.fit(latitudes_reshaped, longitudes)

    first_point_in_fit = [latitudes[0], reg.predict(latitudes_reshaped)[0]]
    last_point_in_fit = [latitudes[-1], reg.predict(latitudes_reshaped)[-1]]
    direction_segment1 = np.array(last_point_in_fit) - np.array(first_point_in_fit)
    direction_segment2 = np.array([curr_lat, curr_long]) - np.array(last_point_in_fit)

    angle = turn_angle(direction_segment1, direction_segment2)
    radians = np.radians(angle) % (2*pi)
    RANGE = pi/3
    if (pi/2 - RANGE <= radians <= pi/2 + RANGE): return "right"
    elif (3*pi/2 - RANGE <= radians <= 3*pi/2 + RANGE): return "left"
    else: return "straight"


def detect_turn(curr_lat, curr_long, file_path, curr_street, api_key, filename):
    prev_longs = np.array([]) # x-val   
    prev_lats = np.array([]) # y-val
    file = open(file_path, 'r')
    paths = file.readlines()
    file.close()
    index = -2
    right_turn_restriction = False

    while(abs(index)<=len(paths)):
        path = paths[index]
        prev_lat, prev_long = path.split(', ')[0], path.split(', ')[1]

        if(path.split(', ')[3].split('Street: ')[1].replace("\n", "")!=curr_street or get_driving_distance(api_key, str(curr_lat)+','+str(curr_long), str(prev_lat)+','+str(prev_long))>150):
            break # different street or too far away
        if(prev_lat!=paths[index+1].split(', ')[0] and prev_long!=paths[index+1].split(', ')[1]):
            prev_longs = np.append(np.array([ float(prev_long.strip())]), prev_longs)
            prev_lats = np.append(np.array([ float(prev_lat.strip())]), prev_lats)
        
        database_turn_restriction = get_database_value(filename, index, "turn_restriction")
        if(right_turn_restriction==False and database_turn_restriction):
            right_turn_restriction = database_turn_restriction
         
        index-=1
    direction = detect_turn_direction(prev_lats, prev_longs, curr_lat, curr_long)
    print("Turn:", direction, right_turn_restriction)
    return direction, right_turn_restriction


# def location(tablename, real_time):
#     load_dotenv(dotenv_path="../.env")
#     API_KEY = os.getenv("HERE_API_KEY")
#     paths_path = "../paths/" + tablename + ".txt"
#     database_path =  "../data/" + tablename + ".db"
#     prev_street, curr_street, turn = "", "", ""
#     intersection = False
#     prev_lat, prev_long = None, None
#     first_coordinate = True

#     os.makedirs('../paths/', exist_ok=True)
    
#     # for coordinates
#     device = get_device()
#     map = folium.Map(
#         location=[device.location()['latitude'], 
#         device.location()['longitude']], 
#         zoom_start=12
#     )
#     stop_sign = False
#     stop_sign_lat, stop_sign_long = 0.0, 0.0
#     stop_sign_id = -1
#     id = -1
#     speed, speed_limit, boolean = None, None, None

    
#     try:
#         while True:
#             if(curr_street):
#                 prev_street = curr_street

#             latitude, longitude = get_coordinates(device, map)
#             print(latitude, longitude)
#             if(get_street_name(API_KEY, latitude, longitude) is None):
#                 continue
            
#             if(os.path.isfile(paths_path)):
#                 speed, speed_limit, boolean = get_speed_info(latitude, longitude, paths_path, real_time)
#                 first_coordinate = False

#             red_light_camera, curr_street = get_red_light_camera(latitude, longitude)
        
#             with open(paths_path, 'a') as file:
#                 file.write(f"{latitude}, {longitude}, Time: {real_time}, Street: {curr_street}\n")
#             with open(paths_path, 'r') as file:
#                 lines = file.readlines()
#                 id = len([line for line in lines if line.strip() != ''])

#             # stop checking for turn after 200m from intersection
#             if(intersection and prev_lat, prev_long and get_driving_distance(API_KEY, \
#                 str(latitude)+","+str(longitude), str(prev_lat)+","+str(prev_long))>200): 
#                 intersection = False

#             # detecting intersection
#             if(intersection==False and prev_street and prev_street!=curr_street):
#                 intersection = True
#                 prev_lat, prev_long = latitude, longitude
            
#             if(intersection): # detecting turn
#                 turn, restriction = detect_turn(latitude, longitude, paths_path, curr_street, API_KEY)
#                 if(restriction!=False and turn == 'right'):
#                     print('Right Turn Violation')

#             # detecting rolling stop  
#             if(stop_sign==False):
#                 if(get_database_value(tablename, -1, "stop_sign") and stop_sign_lat==0.0 and stop_sign_long==0.0): # new stop sign detected
#                    stop_sign = True
#                    stop_sign_lat, stop_sign_long = latitude, longitude
#                    stop_sign_id = get_database_length(tablename)
                                                                            
#             elif(stop_sign==True):
#                 if(get_driving_distance(API_KEY, str(stop_sign_lat)+','+str(stop_sign_long), str(latitude)+','+str(longitude))<=100):
#                     # check for stop
#                     line = ''
#                     with open(paths_path, 'r') as file:
#                         line = file.readlines()[-2]
#                     if(line.split(', ')[0]==latitude and line.split(', ')[1]==longitude):
#                         stop_sign = False
#                         stop_sign_id = -1
#                 else: # didn't stop within 100m after initial detection
#                     update_row("ran_stop_sign", True, stop_sign_id, tablename)
#                     stop_sign = False
#                     stop_sign_lat, stop_sign_long = 0.0, 0.0
#                     stop_sign_id = -1
            
#             if(stop_sign_lat!=0.0 and stop_sign_long!=0 and get_driving_distance(API_KEY, str(stop_sign_lat)+','+str(stop_sign_long), str(latitude)+','+str(longitude))>100):
#                 stop_sign_lat, stop_sign_long = 0.0, 0.0

#             #updating database:
#             update_row("coordinates", str(latitude)+","+str(longitude), id, tablename)
#             update_row("street", curr_street, id, tablename)
#             if(first_coordinate==False):
#                 update_row("speed", speed, id, tablename)
#                 update_row("speed_limit", speed_limit, id, tablename)
#                 update_row("is_speeding", boolean, id, tablename)
#             if(red_light_camera is None):
#                 red_light_camera = "None"
#             update_row("red_light_camera", red_light_camera, id, tablename)

#             time.sleep(INTERVAL)

#     except KeyboardInterrupt:
#         print("Stopping updates.")

    