{
  description = "A PySide6 desktop calendar for CalDAV (Nextcloud) and ICS subscriptions.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
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
          pyPkgs.python-dateutil        # Date utilities
          pyPkgs.recurring-ical-events  # Recurring event expansion (RRULE/RDATE/EXDATE)
          pyPkgs.pytest                 # Test framework
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
          # imagemagick rasterizes the SVG icon into PNG fallbacks at install time (build-time only).
          nativeBuildInputs = [ 
            pkgs.makeWrapper 
            pkgs.imagemagick
          ]; 

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
            cp -r $src/library $out/lib/kubux-calendar/
            cp -r $src/cli $out/lib/kubux-calendar/
            
            # Use makeWrapper to create a final executable 'kubux-calendar' that calls 
            # the python interpreter from the build environment and sets PYTHONPATH.
            makeWrapper ${pythonEnv}/bin/python $out/bin/kubux-calendar \
              --add-flags "$out/lib/kubux-calendar/kubux_calendar.py" \
              --set PYTHONPATH "$out/lib/kubux-calendar"

            # CLI tool to push an .ics file to a calendar
            makeWrapper ${pythonEnv}/bin/python $out/bin/kubux-caldav-send \
              --add-flags "$out/lib/kubux-calendar/cli/__main__.py" \
              --set PYTHONPATH "$out/lib/kubux-calendar"

            # XDG handler: import an .ics attachment into the pending queue
            makeWrapper ${pythonEnv}/bin/python $out/bin/kubux-calendar-attach \
              --add-flags "$out/lib/kubux-calendar/cli/attach_main.py" \
              --set PYTHONPATH "$out/lib/kubux-calendar"

            # Copy desktop file
            cp kubux-calendar.desktop $out/share/applications/
            # XDG .ics handler desktop file — this is the one mimeapps.list
            # should point text/calendar at (NOT kubux-calendar.desktop,
            # which launches the GUI).
            cp kubux-calendar-attach.desktop $out/share/applications/

            # Scalable SVG icon - primary icon; PNG renderings below are
            # the fallback for WMs/desktops that do not handle SVG files.
            mkdir -p $out/share/icons/hicolor/scalable/apps
            cp $src/app-icon.svg $out/share/icons/hicolor/scalable/apps/kubux-calendar.svg

            # Make icon renderings for all sizes (fallback for non-SVG WMs)
            for size in 16x16 22x22 24x24 32x32 48x48 64x64 96x96 128x128 192x192 256x256; do
                mkdir -p $out/share/icons/hicolor/$size/apps
                magick convert $src/app-icon.svg -resize $size $out/share/icons/hicolor/$size/apps/kubux-calendar.png
            done
          '';

          meta = with pkgs.lib; {
            description = "Kubux Calendar: A PySide6 desktop client for Nextcloud CalDAV and ICS feeds.";
            license = licenses.asl20; # Apache License 2.0
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
