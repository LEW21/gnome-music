# Copyright (c) 2013 Arnel A. Borja <kyoushuu@yahoo.com>
# Copyright (c) 2013 Vadim Rutkovsky <vrutkovs@redhat.com>
#
# GNOME Music is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# GNOME Music is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with GNOME Music; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# The GNOME Music authors hereby grant permission for non-GPL compatible
# GStreamer plugins to be used and distributed together with GStreamer
# and GNOME Music.  This permission is above and beyond the permissions
# granted by the GPL license by which GNOME Music is covered.  If you
# modify this code, you may extend this exception to your version of the
# code, but you are not obligated to do so.  If you do not wish to do so,
# delete this exception statement from your version.


from gnomemusic.player import PlaybackStatus, RepeatType
from gnomemusic.albumArtCache import AlbumArtCache
from gnomemusic.grilo import grilo
from gnomemusic.playlists import Playlists

from gettext import gettext as _
from gi.repository import GLib
from gi.repository import Grl
from gi.repository.GLib import Variant
from gnomemusic import log
import logging
logger = logging.getLogger(__name__)

from pydbus import SessionBus
from pydbus.generic import signal
import pkg_resources, os

def expose_service(app):
    bus = SessionBus()
    bus.expose('org.mpris.MediaPlayer2.GnomeMusic', ("/org/mpris/MediaPlayer2", MediaPlayer2Service(app)))

class MediaPlayer2Service:
    MEDIA_PLAYER2_IFACE = 'org.mpris.MediaPlayer2'
    MEDIA_PLAYER2_PLAYER_IFACE = 'org.mpris.MediaPlayer2.Player'
    MEDIA_PLAYER2_TRACKLIST_IFACE = 'org.mpris.MediaPlayer2.TrackList'
    MEDIA_PLAYER2_PLAYLISTS_IFACE = 'org.mpris.MediaPlayer2.Playlists'

    def __repr__(self):
        return '<MediaPlayer2Service>'

    def __init__(self, app):
        self.app = app
        self.player = app.get_active_window().player
        self.player.connect('current-changed', self._on_current_changed)
        self.player.connect('thumbnail-updated', self._on_thumbnail_updated)
        self.player.connect('playback-status-changed', self._on_playback_status_changed)
        self.player.connect('repeat-mode-changed', self._on_repeat_mode_changed)
        self.player.connect('volume-changed', self._on_volume_changed)
        self.player.connect('prev-next-invalidated', self._on_prev_next_invalidated)
        self.player.connect('seeked', self._on_seeked)
        self.player.connect('playlist-changed', self._on_playlist_changed)
        playlists = Playlists.get_default()
        playlists.connect('playlist-created', self._on_playlists_count_changed)
        playlists.connect('playlist-deleted', self._on_playlists_count_changed)
        grilo.connect('ready', self._on_grilo_ready)
        self.playlists = []
        self.playlist = None
        self.playlist_insert_handler = 0
        self.playlist_delete_handler = 0
        self.first_song_handler = 0

    @log
    def _get_playback_status(self):
        state = self.player.get_playback_status()
        if state == PlaybackStatus.PLAYING:
            return 'Playing'
        elif state == PlaybackStatus.PAUSED:
            return 'Paused'
        else:
            return 'Stopped'

    @log
    def _get_loop_status(self):
        if self.player.repeat == RepeatType.NONE:
            return 'None'
        elif self.player.repeat == RepeatType.SONG:
            return 'Track'
        else:
            return 'Playlist'

    @log
    def _get_metadata(self, media=None):
        if not media:
            media = self.player.get_current_media()
        if not media:
            return {}

        metadata = {
            'mpris:trackid': Variant("o", self._get_media_id(media)),
            'xesam:url': Variant("s", media.get_url()),
        }

        try:
            length = media.get_duration() * 1000000
            assert length is not None
            metadata['mpris:length'] = Variant("x", length)
        except:
            pass

        try:
            trackNumber = media.get_track_number()
            assert trackNumber is not None
            metadata['xesam:trackNumber'] = Variant("i", trackNumber)
        except:
            pass

        try:
            useCount = media.get_play_count()
            assert useCount is not None
            metadata['xesam:useCount'] = Variant("i", useCount)
        except:
            pass

        try:
            userRating = media.get_rating()
            assert userRating is not None
            metadata['xesam:userRating'] = Variant("d", userRating)
        except:
            pass

        try:
            title = AlbumArtCache.get_media_title(media)
            assert title is not None
            metadata['xesam:title'] = Variant("s", title)
        except:
            pass

        try:
            album = media.get_album()
            assert album is not None
        except:
            try:
                album = media.get_string(Grl.METADATA_KEY_ALBUM)
                assert album is not None
            except:
                album = _("Unknown Album")
        finally:
            metadata['xesam:album'] = Variant("s", album)

        try:
            artist = media.get_artist()
            assert artist is not None
        except:
            try:
                artist = media.get_string(Grl.METADATA_KEY_ARTIST)
                assert artist is not None
            except:
                try:
                    artist = media.get_author()
                    assert artist is not None
                except (AssertionError, ValueError):
                    artist = _("Unknown Artist")
        finally:
            metadata['xesam:artist'] = Variant("as", [artist])
            metadata['xesam:albumArtist'] = Variant("as", [artist])

        try:
            genre = media.get_genre()
            assert genre is not None
            metadata['xesam:genre'] = Variant("as", [genre])
        except:
            pass

        try:
            lastUsed = media.get_last_played()
            assert lastUsed is not None
            metadata['xesam:lastUsed'] = Variant("s", lastUsed)
        except:
            pass

        try:
            artUrl = media.get_thumbnail()
            assert artUrl is not None
            metadata['mpris:artUrl'] = Variant("s", artUrl)
        except:
            pass

        return metadata

    @log
    def _get_media_id(self, media):
        return '/org/mpris/MediaPlayer2/TrackList/%s' % \
            (media.get_id() if media else 'NoTrack')

    @log
    def _get_media_from_id(self, track_id):
        for track in self.player.playlist:
            media = track[self.player.playlistField]
            if track_id == self._get_media_id(media):
                return media
        return None

    @log
    def _get_track_list(self):
        if self.player.playlist:
            return [self._get_media_id(track[self.player.playlistField])
                    for track in self.player.playlist]
        else:
            return []

    @log
    def _get_playlist_path(self, playlist):
        return '/org/mpris/MediaPlayer2/Playlist/%s' % \
            (playlist.get_id() if playlist else 'Invalid')

    @log
    def _get_playlist_from_path(self, playlist_path):
        for playlist in self.playlists:
            if playlist_path == self._get_playlist_path(playlist):
                return playlist
        return None

    @log
    def _get_playlist_from_id(self, playlist_id):
        for playlist in self.playlists:
            if playlist_id == playlist.get_id():
                return playlist
        return None

    @log
    def _get_playlists(self, callback):
        playlists = []

        def populate_callback(source, param, item, remaining=0, data=None):
            if item:
                playlists.append(item)
            else:
                callback(playlists)

        if grilo.tracker:
            GLib.idle_add(grilo.populate_playlists, 0, populate_callback)
        else:
            callback(playlists)

    @log
    def _get_active_playlist(self):
        playlist = self._get_playlist_from_id(self.player.playlistId) \
            if self.player.playlistType == 'Playlist' else None
        playlistName = AlbumArtCache.get_media_title(playlist) \
            if playlist else ''
        return (playlist is not None,
                (self._get_playlist_path(playlist), playlistName, ''))

    @log
    def _on_current_changed(self, player, data=None):
        if self.player.repeat == RepeatType.SONG:
            self.Seeked(0)

        self.PropertiesChanged(self.MEDIA_PLAYER2_PLAYER_IFACE,
                               {
                                   'Metadata': self._get_metadata(),
                                   'CanPlay': True,
                                   'CanPause': True,
                               },
                               [])

    @log
    def _on_thumbnail_updated(self, player, path, data=None):
        self.PropertiesChanged(self.MEDIA_PLAYER2_PLAYER_IFACE,
                               {
                                   'Metadata': self._get_metadata(),
                               },
                               [])

    @log
    def _on_playback_status_changed(self, data=None):
        self.PropertiesChanged(self.MEDIA_PLAYER2_PLAYER_IFACE,
                               {
                                   'PlaybackStatus': self._get_playback_status(),
                               },
                               [])

    @log
    def _on_repeat_mode_changed(self, player, data=None):
        self.PropertiesChanged(self.MEDIA_PLAYER2_PLAYER_IFACE,
                               {
                                   'LoopStatus': self._get_loop_status(),
                                   'Shuffle': self.player.repeat == RepeatType.SHUFFLE,
                               },
                               [])

    @log
    def _on_volume_changed(self, player, data=None):
        self.PropertiesChanged(self.MEDIA_PLAYER2_PLAYER_IFACE,
                               {
                                   'Volume': self.player.get_volume(),
                               },
                               [])

    @log
    def _on_prev_next_invalidated(self, player, data=None):
        self.PropertiesChanged(self.MEDIA_PLAYER2_PLAYER_IFACE,
                               {
                                   'CanGoNext': self.player.has_next(),
                                   'CanGoPrevious': self.player.has_previous(),
                               },
                               [])

    @log
    def _play_first_song(self, model, path, iter_, data=None):
        if self.first_song_handler:
            model.disconnect(self.first_song_handler)
            self.first_song_handler = 0
        self.player.set_playlist('Songs', None, model, iter_, 5)
        self.player.set_playing(True)

    @log
    def _on_seeked(self, player, position, data=None):
        self.Seeked(position)

    @log
    def _on_playlist_changed(self, player, data=None):
        if self.playlist:
            if self.playlist_insert_handler:
                self.playlist.disconnect(self.playlist_insert_handler)
            if self.playlist_delete_handler:
                self.playlist.disconnect(self.playlist_delete_handler)

        self.playlist = self.player.playlist
        self._on_playlist_modified()

        self.PropertiesChanged(self.MEDIA_PLAYER2_PLAYLISTS_IFACE,
                               {
                                   'ActivePlaylist': self._get_active_playlist(),
                               },
                               [])

        self.playlist_insert_handler = \
            self.playlist.connect('row-inserted', self._on_playlist_modified)
        self.playlist_delete_handler = \
            self.playlist.connect('row-deleted', self._on_playlist_modified)

    @log
    def _on_playlist_modified(self, path=None, _iter=None, data=None):
        if self.player.currentTrack and self.player.currentTrack.valid():
            path = self.player.currentTrack.get_path()
            currentTrack = self.player.playlist[path][self.player.playlistField]
            track_list = self._get_track_list()
            self.TrackListReplaced(track_list, self._get_media_id(currentTrack))
            self.PropertiesChanged(self.MEDIA_PLAYER2_TRACKLIST_IFACE,
                                   {
                                       'Tracks': track_list,
                                   },
                                   [])

    @log
    def _reload_playlists(self):
        def get_playlists_callback(playlists):
            self.playlists = playlists
            self.PropertiesChanged(self.MEDIA_PLAYER2_PLAYLISTS_IFACE,
                                   {
                                       'PlaylistCount': len(playlists),
                                   },
                                   [])

        self._get_playlists(get_playlists_callback)

    @log
    def _on_playlists_count_changed(self, playlists, item):
        self._reload_playlists()

    @log
    def _on_grilo_ready(self, grilo):
        self._reload_playlists()

    def Raise(self):
        self.app.do_activate()

    def Quit(self):
        self.app.quit()

    def Next(self):
        self.player.play_next()

    def Previous(self):
        self.player.play_previous()

    def Pause(self):
        self.player.set_playing(False)

    def PlayPause(self):
        self.player.play_pause()

    def Stop(self):
        self.player.Stop()

    def Play(self):
        if self.player.playlist is not None:
            self.player.set_playing(True)
        elif self.first_song_handler == 0:
            window = self.app.get_active_window()
            window._stack.set_visible_child(window.views[2])
            model = window.views[2].model
            if model.iter_n_children(None):
                _iter = model.get_iter_first()
                self._play_first_song(model, model.get_path(_iter), _iter)
            else:
                self.first_song_handler = model.connect('row-inserted', self._play_first_song)

    def Seek(self, offset):
        self.player.set_position(offset, True, True)

    def SetPosition(self, track_id, position):
        current_track_id = self._get_metadata().get('mpris:trackid')
        if current_track_id:
            current_track_id = current_track_id.unpack()
        if track_id != current_track_id:
            return
        self.player.set_position(position)

    def OpenUri(self, uri):
        pass

    Seeked = signal()

    def GetTracksMetadata(self, track_ids):
        metadata = []
        for track_id in track_ids:
            metadata.append(self._get_metadata(self._get_media_from_id(track_id)))
        return (metadata,)

    def AddTrack(self, uri, after_track, set_as_current):
        pass

    def RemoveTrack(self, track_id):
        pass

    def GoTo(self, track_id):
        for track in self.player.playlist:
            media = track[self.player.playlistField]
            if track_id == self._get_media_id(media):
                self.player.set_playlist(self.player.playlistType,
                                         self.player.playlistId,
                                         self.player.playlist,
                                         track.iter,
                                         self.player.playlistField)
                self.player.play()
                return

    TrackListReplaced = signal()
    TrackAdded = signal()
    TrackRemoved = signal()
    TrackMetadataChanged = signal()

    def ActivatePlaylist(self, playlist_path):
        playlist_id = self._get_playlist_from_path(playlist_path).get_id()
        self.app._window.views[3].activate_playlist(playlist_id)

    def GetPlaylists(self, index, max_count, order, reverse):
        if order != 'Alphabetical':
            return ([],)
        playlists = [(self._get_playlist_path(playlist),
                      AlbumArtCache.get_media_title(playlist) or '', '')
                     for playlist in self.playlists]
        return (playlists[index:index + max_count] if not reverse \
            else playlists[index + max_count - 1:index - 1 if index - 1 >= 0 else None:-1],)

    PlaylistChanged = signal()

# MEDIA_PLAYER2_IFACE:

    CanQuit = True

    @property
    def Fullscreen(self):
        return False

    @Fullscreen.setter
    def Fullscreen(self, val):
        pass

    CanSetFullscreen = False
    CanRaise = True
    HasTrackList = True
    Identity = 'Music'
    DesktopEntry = 'gnome-music'
    SupportedUriSchemes = [
        'file'
    ]
    SupportedMimeTypes = [
        'application/ogg',
        'audio/x-vorbis+ogg',
        'audio/x-flac',
        'audio/mpeg'
    ]

# MEDIA_PLAYER2_PLAYER_IFACE:

    @property
    def PlaybackStatus(self):
        return self._get_playback_status()

    @property
    def LoopStatus(self):
        return self._get_loop_status()

    @LoopStatus.setter
    def LoopStatus(self, val):
        if val == 'None':
            self.player.set_repeat_mode(RepeatType.NONE)
        elif val == 'Track':
            self.player.set_repeat_mode(RepeatType.SONG)
        elif val == 'Playlist':
            self.player.set_repeat_mode(RepeatType.ALL)

    @property
    def Rate(self):
        return 1.0

    @Rate.setter
    def Rate(self, val):
        pass

    @property
    def Shuffle(self):
        return self.player.repeat == RepeatType.SHUFFLE

    @Shuffle.setter
    def Shuffle(self, val):
        if (val and self.player.get_repeat_mode() != RepeatType.SHUFFLE):
            self.set_repeat_mode(RepeatType.SHUFFLE)
        elif val and self.player.get_repeat_mode() == RepeatType.SHUFFLE:
            self.set_repeat_mode(RepeatType.NONE)

    @property
    def Metadata(self):
        return self._get_metadata()

    @property
    def Volume(self):
        return self.player.get_volume()

    @Volume.setter
    def Volume(self, val):
        self.player.set_volume(val)

    @property
    def Position(self):
        return self.player.get_position()

    MinimumRate = 1.0
    MaximumRate = 1.0

    @property
    def CanGoNext(self):
        return self.player.has_next()

    @property
    def CanGoPrevious(self):
        return self.player.has_previous()

    @property
    def CanPlay(self):
        return self.player.currentTrack is not None

    @property
    def CanPause(self):
        return self.player.currentTrack is not None

    CanSeek = True
    CanControl = True

# MEDIA_PLAYER2_TRACKLIST_IFACE:

    @property
    def Tracks(self):
        return self._get_track_list()

    CanEditTracks = False

# MEDIA_PLAYER2_PLAYLISTS_IFACE:

    @property
    def PlaylistCount(self):
        return len(self.playlists)

    Orderings = ('Alphabetical',)

    @property
    def ActivePlaylist(self):
        return self._get_active_playlist()

    PropertiesChanged = signal()

ifaces = ["org.mpris.MediaPlayer2", "org.mpris.MediaPlayer2.Player", "org.mpris.MediaPlayer2.Playlists", "org.mpris.MediaPlayer2.TrackList"]
MediaPlayer2Service.dbus = [pkg_resources.resource_string(__name__, "mpris/" + iface + ".xml").decode("utf-8") for iface in ifaces]
