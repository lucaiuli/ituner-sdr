find_package(Qt6 6.5 REQUIRED COMPONENTS Test)

add_subdirectory(core)
add_subdirectory(transport)
add_subdirectory(audio)
# The Qt Quick layer: the waterfall screen's wiring, plus the end-to-end render
# check against the Python renderer. Both need the Qt Quick items, so they come
# after the GUI-free suites.
find_package(Qt6 6.5 REQUIRED COMPONENTS Quick)
add_subdirectory(ui)
