{ lib, gtk4, glib, gobject-introspection, efibootmgr, python3 }:

python3.pkgs.buildPythonApplication rec {
  name = "efiboots";
  src = ./.;
  buildInputs = [ gtk4 glib gobject-introspection ];
  propagatedBuildInputs = [ efibootmgr ] ++ (with python3.pkgs; [ pygobject3 ]);

  meta = with lib; {
    description = " Manage EFI boot loader entries with this simple GUI";
    homepage = "https://github.com/Elinvention/efibootmgr-gui";
    license = licenses.gpl3;
    platforms = platforms.linux;
  };
  doCheck = true;
}
