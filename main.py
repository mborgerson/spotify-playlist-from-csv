#!/usr/bin/env python
"""
Creates a playlist in Spotify from a list of Artist,Songs in CSV format.
""" 
# Created by Matt Borgerson <mborgerson@gmail.com> on 12/12/2013
#
# Note:
# - Requires a Spotify Premium subscription
# - Requires libspotify (https://developer.spotify.com/technologies/libspotify/)
# - Requires pyspotify 2.x (https://github.com/mopidy/pyspotify)
# - Place spotify_appkey.key in the same directory as this file
# - Tested on Mac OS X 10.9 Mavericks with Python 2.7.5
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import csv
import getpass
import Queue
import spotify
import threading

class Spotify(threading.Thread):
    """Handles all calls to libspotify. libspotify is not thread safe, so any
    call to libspotify must be made by this thread. Simply use the callme method
    to add a callback to the queue of functions to be called by this thread. To
    halt processing of the queue, call the stop method."""

    def __init__(self):
        """Constructor."""
        threading.Thread.__init__(self)

        # Set up the callback queue.
        self._queue_lock = threading.Lock()
        self._queue = Queue.Queue()

        # When set to false, the program will exit. Don't set this directly,
        # use the stop() method instead.
        self._run = True

        # Setup libspotify Callbacks 
        self._callbacks = spotify.SessionCallbacks()
        self._callbacks.notify_main_thread = self.notify_main_thread
        self._callbacks.log_message        = self.log_message

        # Create the session
        self._session = spotify.Session(callbacks=self._callbacks)
        self.notify_main_thread(self._session)

    @property
    def session(self):
        """Simple accessor method for the session."""
        return self._session

    @property
    def callbacks(self):
        """Simple accessor method for the callbacks object. Warning: Do not
        overwrite notify_main_thread."""
        return self._callbacks

    def notify_main_thread(self, session):
        """Facilitates libspotify internal synchronization. Any number of
        threads started by libspotify can call this function. This method is
        responsible for calling session.process_events on the main thread."""
        self.callme(session.process_events)

    def log_message(self, session, data):
        """Log messages from libspotify."""
        print 'libspotify: ' + data

    def callme(self, func, *args, **kargs):
        """Adds a function to the queue of functions to be called later by the
        this thread."""
        self._queue_lock.acquire()
        self._queue.put((func, args, kargs))
        self._queue_lock.release()

    def stop(self):
        """Halt processing of the queue. You should logout before calling this
        to ensure libspotify gets to perform any required synchronizing."""
        def halt(self):
            print 'Halting Spotify Thread'
            self._run = False
        self.callme(halt, self)

    def run(self):
        """Processes the callback queue. Call stop() to halt processing of the
        queue."""
        while self._run:
            cb, args, kargs = self._queue.get()
            cb(*args, **kargs)

class CsvPlaylistCreator(object):
    """Create a Spotify playlist from a CSV file."""

    def __init__(self, spotify_thread):
        """Constructor."""
        self._sp = spotify_thread
        self._searches = None
        self._playlist_created = threading.Event()

    def _start_search(self, pos, artist, song):
        """Starts a search. Note: Should be called by the Spotify thread."""
        query = 'artist:"%s" title:"%s"' % (artist, song)
        self._searches[pos] = self._sp.session.search(query)

    def _create_playlist(self, name, tracks):
        """Creates a playlist in the main container. Note: Should be called by
        the Spotify thread."""
        playlist = self._sp.session.playlist_container.add_new_playlist(name)
        playlist.add_tracks(tracks)
        playlist.load()
        self._playlist_created.set()

    def create(self, csv_file, name):
        """Creates a playlist from a CSV file."""
        # Open the CSV file and read the tracks into memory
        tracks = [track for track in csv.reader(open(csv_file, 'rb'))]
        total = len(tracks)
        self._searches = [None for i in xrange(total)]

        # Enqueue Searches
        print 'Queuing Searches'
        i = 0
        for artist, song in tracks:
            self._sp.callme(self._start_search, i, artist, song)
            i += 1

        # Wait for all searches to complete
        print 'Waiting for searches to complete...'
        loaded = 0
        while loaded < total:
            count = 0
            for s in self._searches:
                # Tally loaded searches
                if s is not None and s.is_loaded:
                    count += 1
            # Report loaded track count updates
            if count > loaded:
                loaded = count
                percent = float(loaded)/float(total)*100.0
                print 'Search %d of %d complete (%.1f%%)' % (loaded, total, percent)

        # Create the playlist
        track_matches, i = [], 0
        for s in self._searches:
            if s.track_total < 1:
                artist, title = tracks[i]
                print 'Note: No matches found for %s - %s...' % (artist, title)
                continue

            track = s.tracks[0]
            artist, album, title = track.artists[0].name, track.album.name, track.name
            track_matches.append(track)
            i += 1

        print 'Creating Playlist...'
        self._sp.callme(self._create_playlist, name, track_matches)
        self._playlist_created.wait()
        print 'Done!'

class App(object):
    """Main application."""

    def __init__(self):
        """Constructor."""
        # Set up synchronization objects for the login
        self.login_error = None
        self.login_finished = threading.Event()
        self.logout_finished = threading.Event()

    def _on_logout_finished(self, session):
        """Callback to be called when libspotify has finished logging out. Used
        for basic synchronization."""
        print 'Logged Out'
        self.logout_finished.set()

    def _on_login_finished(self, session, error):
        """Callback to be called when libspotify has finished logging in. Used
        for basic synchronization."""
        self.login_error = error
        self.login_finished.set()

    def _try_login(self, username='', password='', relogin=False):
        """Attempts logging in. Catches errors and handles basic
        synchronization. Note: Should be called by the Spotify thread."""
        try:
            # Start login
            if relogin: self._sp.session.relogin() # Use previous credentials
            else: self._sp.session.login(username, password, True)
        except spotify.LibError as e:
            self.login_error = e
            self.login_finished.set()

    def _login(self, username='', password='', relogin=False):
        """Starts the login process. Will block until login completes. Returns
        True if login was successful, False otherwise."""
        self._sp.callbacks.logged_in = self._on_login_finished
        self._sp.callbacks.logged_out = self._on_logout_finished

        # Log In
        print 'Logging In...'

        try:
            # Login using Spotify and wait for login to finish
            self._sp.callme(self._try_login, username, password, relogin)
            self.login_finished.wait()

            # Check for errors
            if self.login_error != spotify.LibError.OK:
                print 'Error logging in: ' + self.login_error
                return False

        except spotify.LibError as e:
            print 'An error occured: ' + e
            return False

        print 'Logged In!'

        return True

    def _create(self, file, name):
        """Create a playlist from file."""
        creator = CsvPlaylistCreator(self._sp)
        return creator.create(file, name)

    def run(self, file, name, relogin):
        """Start the main application."""
        username = None
        password = None

        if not relogin:
            # Prompt the user for their username and password
            username = raw_input('Spotify Username: ')
            password = getpass.getpass('Spotify Password: ')

        try:
            # Start the Spotify Thread
            self._sp = Spotify()
            self._sp.start()

            # Login
            if self._login(username, password, relogin):
                # Login successful
                self._create(file, name)

        except KeyboardInterrupt:
            # Catch Keyboard Interrupts (C-c)
            pass
        finally:
            self._sp.callme(self._sp.session.logout)
            print 'Waiting for logout...'
            self.logout_finished.wait()
            self._sp.stop()
            self._sp.join()

def main():
    # Parse Command Line Arguments
    parser = argparse.ArgumentParser(description='Builds a playlist in Spotify by searching for tracks from a CSV file with Artist, Song entries.')
    parser.add_argument('file', help='Input CSV file')
    parser.add_argument('name', help='Name of the playlist to create')
    parser.add_argument('-p',   help='Re-login using the previous credentials',
                                dest='relogin',
                                action='store_true')
    args = parser.parse_args()
    app = App()
    app.run(args.file, args.name, args.relogin)

if __name__ == '__main__':
    main()