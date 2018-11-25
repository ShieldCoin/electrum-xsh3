mkdir -p dist
cd ../..
git pull
pyrcc5 -o contrib/deterministic-build/electrum-xsh-icons/icons_rc.py icons.qrc
pyrcc5 -o gui/qt/icons_rc.py icons.qrc
python setup.py install
cd contrib/build-wine

export NAME_ROOT=electrum-xsh
export VERSION=3.2.2
pyinstaller --noconfirm --ascii --clean --name $NAME_ROOT-$VERSION -w deterministic.spec
"C:/Program Files (x86)/NSIS/makensis.exe" -DPRODUCT_VERSION=3.2.2 electrum.nsi