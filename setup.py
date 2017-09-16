from setuptools import setup


setup(
    name='dirwatch',
    version='0.1',
    packages=['dirwatch'],
    install_requires=['watchdog'],
    entry_points=dict(
        console_scripts=['dirwatch=dirwatch:entry_point']))
