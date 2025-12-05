{
  description = "A PySide6 desktop calendar for CalDAV (Nextcloud) and ICS subscriptions.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # 1. Define the Python environment with necessary third-party libraries (PySide6, CalDAV, ICS).
        pythonEnv = pkgs.python3.withPackages (pyPkgs: [
          pyPkgs.pyside6                # The PySide6 GUI framework
          pyPkgs.requests               # For general HTTP/ICS fetching
          pyPkgs.caldav                 # CalDAV client library
          pyPkgs.ics                    # ICS parsing library
          pyPkgs.icalendar              # iCalendar parsing/generation
          pyPkgs.pytz                   # Timezone handling
          pyPkgs.python-dateutil        # Recurring event expansion (RRULE)
        ]);

      in {
        # 2. Package definition (Minimalist build for distribution)
        packages.default = pkgs.stdenv.mkDerivation {
          pname = "kubux-calendar";
          version = "0.1";
          src = ./.;
          
          # Only the Python environment is needed at runtime.
          buildInputs = [ 
            pythonEnv 
          ];
          
          # makeWrapper is necessary to ensure the packaged script runs with the correct Python interpreter path.
          nativeBuildInputs = [ pkgs.makeWrapper ]; 

          # Installation with all modules:
          installPhase = ''
            # Create directories
            mkdir -p $out/bin
            mkdir -p $out/lib/kubux-calendar
            mkdir -p  $out/share/applications

            # Copy all Python source files
            cp $src/kubux_calendar.py $out/lib/kubux-calendar/
            cp -r $src/backend $out/lib/kubux-calendar/
            cp -r $src/gui $out/lib/kubux-calendar/
            
            # Use makeWrapper to create a final executable 'kubux-calendar' that calls 
            # the python interpreter from the build environment and sets PYTHONPATH.
            makeWrapper ${pythonEnv}/bin/python $out/bin/kubux-calendar \
              --add-flags "$out/lib/kubux-calendar/kubux_calendar.py" \
              --set PYTHONPATH "$out/lib/kubux-calendar"

            # Copy desktop file
            cp kubux-calendar.desktop $out/share/applications/
          '';

          meta = with pkgs.lib; {
            description = "Kubux Calendar: A PySide6 desktop client for Nextcloud CalDAV and ICS feeds.";
            license = licenses.gpl3Only; # Using a common FLOSS license as a placeholder
            platforms = platforms.linux;
          };
        };

        # 3. Development Shell definition
        devShells.default = pkgs.mkShell {
          # Include the Python environment and any minimal developer tools.
          buildInputs = [
            pythonEnv
          ];
          shellHook = ''
            echo "Welcome to the Kubux Calendar development shell!"
            echo "Python environment is ready with PySide6, caldav, and ics."
          '';
        };
      });   
}
