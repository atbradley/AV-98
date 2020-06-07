from setuptools import setup

setup(
    name='AV-98',
    version='1.0.0',
    description="Command line Gemini client.",
    author="Solderpunk",
    author_email="solderpunk@sdf.org",
    url='https://tildegit.org/solderpunk/AV-98/',
    classifiers=[
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Communications',
        'Intended Audience :: End Users/Desktop',
        'Environment :: Console',
        'Development Status :: 4 - Beta',
    ],
    py_modules = ["av98"],
    entry_points={
        "console_scripts": ["av98=av98:main"]
    },
    install_requires=[],
)
