from setuptools import setup


setup(
    name='dirwatch',
    version='0.1',
    packages=['dirwatch'],
    install_requires=[
        'pyfswatch @ git+https://github.com/paul-nameless/pyfswatch'],
    entry_points=dict(
        console_scripts=['dirwatch=dirwatch:entry_point']))
