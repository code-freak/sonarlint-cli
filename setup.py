from setuptools import setup, find_packages

setup(
    name='sonarlintcli',
    version='0.0.0',
    py_modules=['sonarlintcli'],
    packages=find_packages(),
    package_data={
        'sonarlintcli' : ['sonarlint/server/*.jar', 'sonarlint/analyzers/*.jar']
    },
    install_requires=[
        'Click',
    ],
    entry_points='''
        [console_scripts]
        sonarlint-cli=sonarlintcli.cli:main
    ''',
)