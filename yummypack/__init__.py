import pkgutil

__certificate__ = pkgutil.get_data("yummypack", "cacert.pem")
__version__ = pkgutil.get_data("yummypack", "version")


def certificate():
    return __certificate__


def version():
    return __version__
