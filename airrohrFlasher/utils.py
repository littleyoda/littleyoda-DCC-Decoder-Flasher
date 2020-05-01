import requests
import re
import logging
import csv
import urllib
import json
from .qtvariant import QtCore
import urllib.request
from contextlib import closing


#file_index_re = re.compile(r'<a href="([^"]*)">([^<]*)</a>')


def indexof(url):
    r = requests.get(url=url)
    json = r.json();
    data = [ [item['board'], item['version'], item['url']] for item in json['firmware']]
    return data


class QuickThread(QtCore.QThread):
    error = QtCore.Signal([str])

    """Provides similar API to threading.Thread but with additional error
    reporting based on Qt Signals"""
    def __init__(self, parent=None, target=None, args=None, kwargs=None,
                 error=None):
        super(QuickThread, self).__init__(parent)
        self.target = target or self.target
        self.args = args or []
        self.kwargs = kwargs or {}
        self.error = error or self.error

    def run(self):
        try:
            self.target(*self.args, **self.kwargs)
        except Exception as exc:
            if self.error:
                self.error.emit(str(exc))
            # raise here causes windows builds to just die. ¯\_(ツ)_/¯
            logging.exception('Unhandled exception')

    @classmethod
    def wrap(cls, func):
        """Decorator that wraps function in a QThread. Calling resulting
        function starts and creates QThread, with parent set to [self]"""
        def wrapped(*args, **kwargs):
            th = cls(parent=args[0], target=func, args=args, kwargs=kwargs,
                     error=kwargs.pop('error', None))
            func._th = th
            th.start()

            return th

        wrapped.running = lambda: (hasattr(func, '_th') and
                                   func._th.isRunning())
        return wrapped

    def target(self):
        pass
