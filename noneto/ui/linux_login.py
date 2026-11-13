"""Private Qt WebEngine login window, used only inside the isolated Linux worker."""


def render_login(url, redirect):
    from PyQt6.QtCore import QTimer, QUrl, pyqtSignal
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile,
                                      QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor)
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    from ..i18n import _

    app = QApplication(["noneto-login"])
    app.setApplicationName("Noneto CLI")
    view = QWebEngineView()
    view.setWindowTitle(_("Sign in to TIDAL - Noneto CLI"))
    view.resize(500, 780)
    # A profile without a storage name is off-the-record: cookies/cache stay in memory.
    profile = QWebEngineProfile(view)
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
    page = QWebEnginePage(profile, view)
    view.setPage(page)
    captured = []

    class Interceptor(QWebEngineUrlRequestInterceptor):
        authorized = pyqtSignal(str)

        def interceptRequest(self, info):
            target = info.requestUrl().toString()
            if (info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame
                    and len(target) <= 16384 and target.startswith(redirect + "?")):
                info.block(True)
                self.authorized.emit(target)

    def finish(target):
        if not captured:
            captured.append(target)
        view.close()
        app.quit()

    interceptor = Interceptor(profile)
    interceptor.authorized.connect(finish)
    profile.setUrlRequestInterceptor(interceptor)
    # Bound the window lifetime even if the owner disappears. No event loop survives the worker.
    import os
    owner = os.getppid()
    timer = QTimer(view)
    timer.timeout.connect(lambda: view.close() if os.getppid() != owner else None)
    timer.start(500)
    view.load(QUrl(url))
    view.show()
    app.exec()
    # Delete the page before its profile, so teardown cannot keep renderer processes alive.
    view.setPage(None)
    page.deleteLater()
    app.processEvents()
    return captured[0] if captured else ""
