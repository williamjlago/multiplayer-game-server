Will Lago

COS 460

12/2/25


# Project 4 - Game Server+

Simple 2D grid-based game server. Handles connections/disconnections, login, movement, map display/updates, player-to-player messages, and info requests such as INFO and LEADERBOARD.
Also now includes a working ACTION command with two action types: CLASS (changes class when in spawn) and ATTACK (attack or heal another player).

Three classes:
* Knight: 200 HP, can only target adjacent or diagonal enemies. 60 damage per attack.
* Archer: 100 HP, can target enemies up to 5 tiles away. 40 damage per attack.
* Cleric: 125 HP, can target allies up to 5 tiles away. 50 healing per "attack".

The main objective of the game is to hold the control zones, which start out neutral and can be captured over time by standing within them. Multiple players of the same team will speed up the capture, but any players of the opposing team will halt progress until they are removed. Each team has a point counter (sent with INFO) that increases every second based on the number of control zones owned, and the first team to reach 500 total points wins the game (note that these are distinct from player score, which essentially acts as a kill counter in this version).


To run, navigate to the directory containing game_server.py and enter:
```
python game_server.py <port>
```
to start the server on the specified localhost port, or
```
python game_server.py <host> <port>
```
to start the server on the specified port of the specified remote host.

Note that a valid map.txt must also be present in the working directory.


The client can then be connected to via a terminal network utility like telnet or netcat.

---

# Questions

* What is the general outline of the client-server protocol; text, binary, TCP, UDP?

The server itself uses the basic Java socket library, which uses a TCP connection. The protocol is lightweight and plaintext-based, with no headers; requests consist of a request type (first word of the request) and parameters (all subsequent words). Responses consist of a response code (100, 200, 400, etc.), a response type (corresponding to the client request type), and a brief description of the response (OK, Failure, etc.).


* How does a connected client “login” as a player?

The only real difference between a logged in an non-logged in player is whether or not they've chosen a name; the Player object is created as soon as a client connects, but it isn't displayed on the map or able to interact with other connected players until a successful LOGIN.


* Are there other things in the game other than players? NPC/AI?

No plans for NPC/AI entities; my idea for Project 4 is strictly player-versus-player.


How does the server “show” the player the map or board?

The map is printed line-by-line, with tiles occupied by players dynamically replaced by the first letter of that player's name. Since the display of players is done dynamically by the map display function, the original map tiles are never overwritten by player tiles.


* What happens when a player makes an invalid move?

A 400 response is returned, and the player's position is not updated.


* What happens to players that “disappear” (e.g. connection loss)?

A player that disconnects by any means, be that a QUIT request, client termination, or connection loss, has their Player object removed from the list and effectively no longer exists as far as the server is concerned.
