from queue import Queue
import socket
import threading
import time
import sys
import re
import random
from typing import Tuple

class Player:
    def __init__(self, conn, addr):
        self.conn = conn
        self.address = addr
        self.recv_buffer = ""
        self.name = ""
        self.pos_x = 0
        self.pos_y = 0
        self.score = 0
        self.health = 100
        self.max_health = 100
        self.state = "active"
        self.character_class = ""
        self.team = 0
        self.spawn_timer = 0
        self.quitting = False
        self.outgoing_queue = Queue(maxsize=1000)

class ControlZone:
    def __init__(self, id:int, topleft:Tuple, bottomright:Tuple):
        self.zone_id = id
        self.topleft = topleft
        self.bottomright = bottomright
        self.zone_tiles = []
        self.players_in_zone = []
        self.owner = 0 # 0 = uncontrolled, 1 = controlled by team 1, 2 = controlled by team 2
        self.capture_progress = 0
        # populate all tiles in zone based on corners
        y_count = topleft[1]
        while y_count <= bottomright[1]:
            x_count = topleft[0]
            while x_count <= bottomright[0]:
                tile = (x_count, y_count)
                self.zone_tiles.append(tile)
                x_count += 1
            y_count += 1

players: list[Player] = []
players_lock = threading.Lock()
server_name = "GameServer"
game_map = []
game_timer = 0
map_width = 0
map_height = 0
team1_score = 0
team2_score = 0
zones = []

def load_map(filename):
    # Read initial map from map.txt
    map_tiles = []
    with open(filename, 'r') as f:
        for line in f:
            line = line.rstrip('\n')
            if not map_tiles:
                width = len(line)
            elif len(line) != width:
                raise ValueError("Map is not rectangular")
            map_tiles.append(list(line))
    return map_tiles

def initialize_player_position(player):
    # Place newly spawned players on a random tile in their team's spawn area
    x_pos = 0
    y_pos = 0
    found_valid_pos = False
    while not found_valid_pos:
        if player.team == 1:
            x_pos = random.randrange(1, 5)
            y_pos = random.randrange(7, 13)
            found_valid_pos = is_passable(x_pos, y_pos, player)
        elif player.team == 2:
            x_pos = random.randrange(67, 71)
            y_pos = random.randrange(7, 13)
            found_valid_pos = is_passable(x_pos, y_pos, player)
    player.pos_x = x_pos
    player.pos_y = y_pos
    player_update(player)

def is_passable(x, y, player) -> bool:
    # Checks whether a tile is blocked by an obstacle or other player
    player_occupying = False
    try:
        if game_map[y][x] != '#' and game_map[y][x] != '~':
            if (player.team == 1 and game_map[y][x] != '\"') or (player.team == 2 and game_map[y][x] != '\''):
                with players_lock:
                    for p in players:
                        if p.pos_x == x and p.pos_y == y and p.state == "active":
                            player_occupying = True
                    if not player_occupying:
                        return True
    except IndexError:
        return False
    return False

def send_response(player, code, msg):
    # Send responses to direct requests
    response = f"{str(code)} {str(msg)}\n"
    player.conn.sendall(response.encode('utf-8'))
    return

def client_connection(conn, addr):
    # Sets up connection between client and server
    try:
        this_player = Player(conn, addr)
        sender = threading.Thread(target=send_outgoing, args=(this_player,))
        sender.daemon = True
        sender.start()
        add_player(this_player)
        while not this_player.quitting:
            handle_request(this_player)
    finally:
        if this_player in players:
            remove_player(this_player)
        conn.close()
        # Note that a PLAYER update is not sent when a player disconnects, since their player object is
        # deleted and there is nothing to send. A 100 info message is sent instead.
        if len(this_player.name) > 0:
            broadcast(f"100 Player {this_player.name} disconnected")

def handle_request(this_player):
    # User input handling function
    data = this_player.conn.recv(2048)
    if not data:
        this_player.quitting = True
        return

    this_player.recv_buffer += data.decode('utf-8', errors='replace')

    while '\n' in this_player.recv_buffer:
        line, this_player.recv_buffer = this_player.recv_buffer.split('\n', 1)
        line = line.strip()
        if not line:
            continue
        handle_request_line(this_player, line)

def handle_request_line(this_player, request_line):
    parts = request_line.split()
    if not parts:
        return
    request_type = parts[0]
    del parts[0]
    args = parts

    # Handle different request types here
    if request_type == "QUIT":
        # exit on QUIT even if not logged in
        this_player.quitting = True
        return
    if request_type != "LOGIN" and len(this_player.name) < 1:
        response_msg = f"Failed, LOGIN first"
        send_response(this_player, 400, response_msg)
        return
    if request_type == "LOGIN":
        if len(args) != 1:
            response_msg = f"{str(request_type)} Failed, use LOGIN <name>"
            send_response(this_player, 400, response_msg)
            return
        if len(this_player.name) > 0:
            response_msg = f"{str(request_type)} Failed, already logged in as {str(this_player.name)}"
            send_response(this_player, 400, response_msg)
            return
        new_name = args[0]
        if not re.fullmatch(r"[a-zA-Z0-9_]+", new_name):
            response_msg = f"{str(request_type)} Failed, name contains invalid characters"
            send_response(this_player, 400, response_msg)
            return
        with players_lock:
            for player in players:
                if player.name.lower() == new_name.lower():
                    response_msg = f"{str(request_type)} Failed, name {str(player.name)} is already in use"
                    send_response(this_player, 400, response_msg)
                    return
        this_player.name = new_name
        assign_team(this_player)
        rand_class = random.randrange(1, 4)
        match rand_class:
            case 1:
                change_class(this_player, "knight")
            case 2:
                change_class(this_player, "archer")
            case 3:
                change_class(this_player, "cleric")

        response_msg = f"{str(request_type)} OK, hello {str(this_player.name)}"
        send_response(this_player, 200, response_msg)
        response_msg = f"You have been assigned to team {str(this_player.team)}"
        send_response(this_player, 100, response_msg)
        response_msg = f"You have been assigned the {str(this_player.character_class)} class; use ACTION CLASS <knight,archer,cleric> while in spawn area to change."
        send_response(this_player, 100, response_msg)
        initialize_player_position(this_player)
        send_map(this_player)
        return
    if request_type == "MESSAGE":
        if len(args) < 2:
            response_msg = f"{str(request_type)} Failed, use MESSAGE <player-name> <msg>"
            send_response(this_player, 400, response_msg)
            return
        else:
            # Concatenate all arguments after the first into one string to be sent as a message
            message = ""
            word_count = 1
            while word_count < len(args):
                for char in args[word_count]:
                    if char == '\r' or char == '\n':
                        response_msg = f"{str(request_type)} Failed, message cannot contain return or newline characters"
                        send_response(this_player, 400, response_msg)
                message = message + args[word_count]
                if word_count != (len(args) - 1):
                    # if this is not the last word, add a space
                    message = message + " "
                word_count += 1
            dest_name = args[0]
            with players_lock:
                for player in players:
                    if player.name.lower() == dest_name.lower():
                        player.outgoing_queue.put(f"200 MESSAGE {this_player.name},{message}\n")
                        return
            response_msg = f"{str(request_type)} Failed, player {dest_name} not found"
            send_response(this_player, 400, response_msg)
    if request_type == "MAP":
        send_map(this_player)
        return
    if request_type == "MAPU":
        # Request a map tile update; I don't think this is actually part of the protocol,
        # but it doesn't hurt to include it in case it becomes useful later on
        # Also the only way to test MAPU responses at this stage
        if len(args) != 1:
            response_msg = f"{str(request_type)} Failed, use MAPU <x>,<y>,<new-tile-char>"
            send_response(this_player, 400, response_msg)
            return
        data = args[0].split(',')
        if len(data) != 3:
            response_msg = f"{str(request_type)} Failed, use MAPU <x>,<y>,<new-tile-char>"
            send_response(this_player, 400, response_msg)
            return
        try:
            update_x = int(data[0])
            update_y = int(data[1])
        except ValueError:
            response_msg = f"{str(request_type)} Failed, x and y must be integers"
            send_response(this_player, 400, response_msg)
            return
        new_tile_char = data[2]
        update_map_tile(update_x, update_y, new_tile_char)
        return
    if request_type == "MOVE":
        if len(args) != 1:
            response_msg = f"{str(request_type)} Failed, use MOVE <dx>,<dy>"
            send_response(this_player, 400, response_msg)
            return
        move_vec = args[0].split(',')
        if len(move_vec) != 2:
            response_msg = f"{str(request_type)} Failed, use MOVE <dx>,<dy>"
            send_response(this_player, 400, response_msg)
            return
        try:
            dx = int(move_vec[0])
            dy = int(move_vec[1])
        except ValueError:
            response_msg = f"{str(request_type)} Failed, dx and dy must be integers"
            send_response(this_player, 400, response_msg)
            return
        if this_player.state == "inactive":
            response_msg = f"{str(request_type)} Failed, you are dead"
            send_response(this_player, 400, response_msg)
            return
        handle_move(this_player, dx, dy)
        return
    if request_type == "INFO":
        response_msg = f"INFO {server_name},{map_width},{map_height},{game_timer},{str(len(players))}"
        send_response(this_player, 200, response_msg)
        return
    if request_type == "LEADERBOARD":
        lb_players = []
        with players_lock:
            for player in players:
                lb_players.append(player)
        for player in lb_players:
            player_update(player)
        return
    if request_type == "ACTION":
        special_action(this_player, args)
        return
    else:
        response_msg = f"Bad request \"{str(request_type)}\""
        send_response(this_player, 400, response_msg)
    return

def timer():
    # Increment the game timer by one every second as long as the server is running
    global game_timer
    global team1_score
    global team2_score
    while True:
        time.sleep(1)
        game_timer += 1

        inactive_players = []
        with players_lock:
            for player in players:
                if player.state == "inactive":
                    inactive_players.append(player)
        for player in inactive_players:
            if player.spawn_timer > 0:
                player.spawn_timer -= 1
            else:
                initialize_player_position(player)
                player.state = "active"

        for zone in zones:
            if zone.owner == 1:
                team1_score += 1
            elif zone.owner == 2:
                team2_score += 1

        # server just exits when one team wins, but logic could be added to vote to
        # start a new game, choose a new map, etc.
        if team1_score >= 500:
            broadcast("100 Team 1 wins!")
            sys.exit(0)
        elif team2_score >= 500:
            broadcast("100 Team 2 wins!")
            sys.exit(0)

        if game_timer % 20 == 0:
            # Send INFO updates to all players every 20 seconds by default
            # These would be more frequent with an actual game client; low frequency for telnet testing purposes
            broadcast(f"200 INFO {server_name},{map_width},{map_height},{game_timer},{team1_score},{team2_score},{str(len(players))}")

        # check if any players are within control zones
        zones_to_capture = []
        with players_lock:
            for player in players:
                player_tile = (player.pos_x, player.pos_y)
                for zone in zones:
                    if player_tile in zone.zone_tiles and player.state == "active":
                        if not player in zone.players_in_zone:
                            zone.players_in_zone.append(player)
                    else:
                        if player in zone.players_in_zone:
                            zone.players_in_zone.remove(player)
            for zone in zones:
                if len(zone.players_in_zone) == 0:
                    match zone.owner:
                        case 0:
                            # reset progress to 0 over time if a neutral zone is partially captured
                            if zone.capture_progress < 0:
                                zone.capture_progress += 5
                                if zone.capture_progress > 0:
                                    zone.capture_progress = 0
                            elif zone.capture_progress > 0:
                                zone.capture_progress -= 5
                                if zone.capture_progress < 0:
                                    zone.capture_progress = 0
                        case 1:
                            # reset progress to -100 if a team 1 owned zone is partially captured
                            if zone.capture_progress > -100:
                                zone.capture_progress -= 5
                                if zone.capture_progress < -100:
                                    zone.capture_progress = -100
                        case 2:
                            # reset progress to 100 if a team 2 owned zone is partially captured
                            if zone.capture_progress < 100:
                                zone.capture_progress += 5
                                if zone.capture_progress > 100:
                                    zone.capture_progress = 100
                else:
                    all_same_team = True
                    first_player = zone.players_in_zone[0]
                    for player in zone.players_in_zone:
                        if first_player.team != player.team:
                            all_same_team = False
                    if all_same_team:
                        if first_player.team == 1 and zone.owner != 1:
                            zone.capture_progress -= (8 * len(zone.players_in_zone))
                            if zone.capture_progress <= -100:
                                zone.capture_progress = -100
                                zones_to_capture.append((zone, 1))
                        elif first_player.team == 2 and zone.owner != 2:
                            zone.capture_progress += (8 * len(zone.players_in_zone))
                            if zone.capture_progress >= 100:
                                zone.capture_progress = 100
                                zones_to_capture.append((zone, 2))
        for zone, team in zones_to_capture:
            capture_zone(zone,team)

def handle_move(player, dx, dy):
    # Process MOVE requests
    old_x = player.pos_x
    old_y = player.pos_y
    new_x = old_x + dx
    new_y = old_y + dy
    if not is_passable(new_x, new_y, player):
        player.outgoing_queue.put("400 MOVE Failed: impassible or occupied tile\n")
        return
    player.pos_x = new_x
    player.pos_y = new_y
    player_update(player)
def add_player(player):
    # Thread safe function for adding players to player list
    with players_lock:
        players.append(player)

def remove_player(player):
    # Thread safe function for removing players from player list
    with players_lock:
        if player in players:
            players.remove(player)

def player_update(player):
    broadcast(f"200 PLAYER {player.name},{player.pos_x},{player.pos_y},{player.team},{player.character_class},{player.health},{player.max_health},{player.state},{player.score}")
    return

def send_outgoing(player):
    # Send any queued outgoing asynchronous messages
    conn = player.conn
    while not player.quitting:
        msg = player.outgoing_queue.get()
        try:
            conn.sendall(msg.encode('utf-8'))
        except (OSError, socket.timeout):
            player.quitting = True
            return

def send_map(player):
    # Send a client the current map, including the locations of players
    row_num = 0
    for row in game_map:
        char_num = 0
        msg = f"MAP {row_num + 1:03d},"
        for char in row:
            player_occupying = False
            with players_lock:
                for p in players:
                    if p.pos_x == char_num and p.pos_y == row_num and p.state == "active" and len(p.name) > 0:
                        msg = msg + str(p.name[0])
                        player_occupying = True
            if not player_occupying:
                msg = msg + str(char)
            char_num += 1
        player.outgoing_queue.put("200 " + msg + "\n")
        row_num += 1
    return

def update_map_tile(x, y, new_tile):
    global game_map
    game_map[y][x] = new_tile
    broadcast(f"200 MAPU {x},{y},{new_tile}")
    return

def broadcast(msg):
    # Send a message to all logged in players
    with players_lock:
        for player in players:
            if len(player.name) > 0:
                # Only receive message if logged in
                try:
                    player.outgoing_queue.put(f"{msg}\n")
                except:
                    player.quitting = True

def special_action(player, args):
    if len(args) < 1:
        response_msg = f"ACTION Failed, no arguments given"
        send_response(player, 400, response_msg)
        return
    if player.state == "inactive":
        response_msg = f"ACTION Failed, you are dead"
        send_response(player, 400, response_msg)
        return
    if args[0] == "CLASS":
        if len(args) == 2 and ((args[1] == "knight") or (args[1] == "archer") or (args[1] == "cleric")):
            if (player.team == 1 and game_map[player.pos_y][player.pos_x] != '\'') or (player.team == 2 and game_map[player.pos_y][player.pos_x] != '\"'):
                response_msg = f"ACTION Failed, cannot change class outside of spawn area"
                send_response(player, 400, response_msg)
                return
            change_class(player, args[1])
            response_msg = f"ACTION OK, changed class to {player.character_class}"
            send_response(player, 200, response_msg)
            return
        else:
            response_msg = f"ACTION Failed, malformed class change request"
            send_response(player, 400, response_msg)
            return
    elif args[0] == "ATTACK":
        if len(args) == 2:
            target_player = None
            with players_lock:
                for p in players:
                    if p.name == args[1]:
                        if player.name == p.name:
                            response_msg = f"ACTION Failed, cannot attack self"
                            send_response(player, 400, response_msg)
                            return
                        if player.state == "inactive":
                            response_msg = f"ACTION Failed, target is dead"
                            send_response(player, 400, response_msg)
                            return
                        target_player = p
            if target_player:
                launch_attack(player, target_player)
                return
            else:
                response_msg = f"ACTION Failed, attack target not found"
                send_response(player, 400, response_msg)
                return
        else:
            response_msg = f"ACTION Failed, malformed attack request"
            send_response(player, 400, response_msg)
            return
    else:
        response_msg = f"ACTION Failed, unknown action type"
        send_response(player, 400, response_msg)
        return

def assign_team(player):
    num_players_team1 = 0
    num_players_team2 = 0
    with players_lock:
        for player in players:
            if player.team == 1:
                num_players_team1 += 1
            elif player.team == 2:
                num_players_team2 += 1
        if num_players_team1 == num_players_team2:
            # assign a new player to a random team if teams are balanced
            rand_team = random.randrange(1,3)
            player.team = rand_team
            return
        elif num_players_team1 < num_players_team2:
            # assign a new player to team 1 if team 2 has more players
            player.team = 1
        else:
            # assign a new player to team 1 if team 2 has more players
            player.team = 2

def change_class(player, char_class):
    match char_class:
        case "knight":
            player.character_class = char_class
            player.max_health = 200
            player.health = 200
        case "archer":
            player.character_class = char_class
            player.max_health = 100
            player.health = 100
        case "cleric":
            player.character_class = char_class
            player.max_health = 125
            player.health = 125

def capture_zone(zone, team):
    zone.players_in_zone.clear()
    zone.capture_progress = -100 if team == 1 else 100
    zone.owner = team
    tile_char = "-" if team == 1 else "+"
    for x, y in zone.zone_tiles:
        game_map[y][x] = tile_char
    tiles_joined = ";".join(f"{x}:{y}" for x, y in zone.zone_tiles)
    msg = f"200 MAPZ {zone.zone_id},{team},{tile_char},{tiles_joined}"
    broadcast(msg)

def launch_attack(attacking_player, target_player):
    match attacking_player.character_class:
        case "knight":
            # knight can only attack adjacent units
            if attacking_player.team == target_player.team:
                response_msg = f"ACTION Failed, cannot attack teammates"
                send_response(attacking_player, 400, response_msg)
                return
            dx = attacking_player.pos_x - target_player.pos_x
            dy = attacking_player.pos_y - target_player.pos_y
            if abs(dx) > 1 or abs(dy) > 1:
                response_msg = f"ACTION Failed, attack target out of range"
                send_response(attacking_player, 400, response_msg)
                return
            modify_health(target_player, attacking_player, -60)
            response_msg = f"ACTION OK, successful attack on {target_player.name}"
            send_response(attacking_player, 200, response_msg)
            return
        case "archer":
            # archer can attack up to 5 spaces away
            if attacking_player.team == target_player.team:
                response_msg = f"ACTION Failed, cannot attack teammates"
                send_response(attacking_player, 400, response_msg)
                return
            if not in_range(attacking_player, target_player):
                response_msg = f"ACTION Failed, attack target out of range"
                send_response(attacking_player, 400, response_msg)
                return
            if not has_line_of_sight(attacking_player, target_player):
                response_msg = f"ACTION Failed, no line of sight to attack target"
                send_response(attacking_player, 400, response_msg)
                return
            modify_health(target_player, attacking_player, -40)
            response_msg = f"ACTION OK, successful attack on {target_player.name}"
            send_response(attacking_player, 200, response_msg)
            return

        case "cleric":
            # cleric can heal up to 5 spaces away
            if attacking_player.team != target_player.team:
                response_msg = f"ACTION Failed, cannot heal enemies"
                send_response(attacking_player, 400, response_msg)
                return
            if not in_range(attacking_player, target_player):
                response_msg = f"ACTION Failed, heal target out of range"
                send_response(attacking_player, 400, response_msg)
                return
            if not has_line_of_sight(attacking_player, target_player):
                response_msg = f"ACTION Failed, no line of sight to heal target"
                send_response(attacking_player, 400, response_msg)
                return
            modify_health(target_player, attacking_player, 50)
            response_msg = f"ACTION OK, successful heal on {target_player.name}"
            send_response(attacking_player, 200, response_msg)
            return

def modify_health(player, source, value):
    player.health += value
    if player.health > player.max_health:
        player.health = player.max_health
    elif player.health <= 0:
        player.health = 0
        player.state = "inactive"
        player.spawn_timer = 10
        source.score += 1
        player_update(source)
    player_update(player)
    return

def has_line_of_sight(attacking_player, target_player):
    for x, y in bresenham_line(attacking_player.pos_x, attacking_player.pos_y, target_player.pos_x, target_player.pos_y):
        if game_map[y][x] == '#':
            return False
    return True

def in_range(p1, p2):
    # archers and clerics must be within 5 tiles of the target to attack/heal
    path_length = 0
    for x, y in bresenham_line(p1.pos_x, p1.pos_y, p2.pos_x, p2.pos_y):
        path_length += 1
    if path_length <= 5:
        return True
    else:
        return False

def bresenham_line(x0, y0, x1, y1):
    # yield tiles in shortest line between source and destination tile
    # used in line of sight check for archers/clerics
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    x, y = x0, y0
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1

    if dx > dy:
        err = dx // 2
        while x != x1:
            x += sx
            err -= dy
            if err < 0:
                y += sy
                err += dx
            yield x, y
    else:
        err = dy // 2
        while y != y1:
            y += sy
            err -= dx
            if err < 0:
                x += sx
                err += dy
            yield x, y

def serve(host='0.0.0.0', port=3377):
    # Start the server and prepare to receive client connections
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, port))
    s.listen(5)
    timer_thread = threading.Thread(target=timer)
    timer_thread.start()
    print(f"Hosting game on {host}:{port}...")
    while True:
        conn, addr = s.accept()
        print(f"Client {str(addr)} connected")
        thread = threading.Thread(target=client_connection, args=(conn, addr))
        thread.start()

if __name__ == "__main__":
    game_map = load_map("map.txt")
    map_height = len(game_map)
    map_width = len(game_map[0])
    zones.append(ControlZone(1, (12, 1), (15, 4)))
    zones.append(ControlZone(2, (34, 8), (37, 11)))
    zones.append(ControlZone(3, (56, 15), (59, 18)))

    if len(sys.argv) == 2:
        port = int(sys.argv[1])
        serve(port=port)
    elif len(sys.argv) == 3:
        host = sys.argv[1]
        port = int(sys.argv[2])
        serve(host, port)
    else:
        print("Usage: python game_server.py <port> for localhost, or python game_server.py <host> <port> to start server on the specified host")
        sys.exit(1)