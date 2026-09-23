import sublime
import sublime_plugin


def plugin_loaded():
    for window in sublime.windows():
        window.set_menu_visible(False)


class HideMenuOnNewWindow(sublime_plugin.EventListener):
    def on_new_window(self, window):
        window.set_menu_visible(False)
