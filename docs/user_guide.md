# User Guide

ST-Pitch provides a web interface for registering soccer match event CSV files, organizing them into tournaments/groups, and analyzing the data on an interactive soccer field.

## Overview

The main page provides four main entry points:

- Tournament and match selection
- Interactive GIS analysis
- CSV file upload
- Tournament management

![Top page](images/usage_00_top.png)

## Recommended Workflow

For first-time use, the recommended order is:

1. Create a tournament
2. Upload event CSV files
3. Register uploaded matches to a tournament
4. Open GIS analysis for a selected match
5. Narrow the target area or conditions with filters
6. Extract and review event sequences when needed

## 1. Create a Tournament

Use the **Add New Tournament** button on the top page to create a tournament container for your matches. A tournament can represent a specific competition, season, or any grouping of matches you want to analyze together.

On the Add Tournament page, enter:

- Tournament Name
- Year
- Start Date
- End Date

![Add tournament form](images/usage_01_group_1.png)
Then click **Register Tournament**.

After registration, the tournament appears in the Registered Tournaments list.

## 2. Upload Event CSV Files

From the top page, use the **CSV File Upload** section to upload one or more event CSV files.

![CSV upload section](images/usage_03_upload_1.png)


The CSV files must contain the required columns shown in the UI:

| Column   | Description                         |
|----------|-------------------------------------|
| Type     | Event type                          |
| Side1    | Team side of the acting player      |
| Unum1    | Uniform number of the acting player |
| Time1    | Start time of the event             |
| Mode1    | Play mode at event start            |
| X1, Y1   | Start position on the field         |
| Side2    | Team side of the receiving player   |
| Unum2    | Uniform number of the receiving player |
| Time2    | End time of the event               |
| X2, Y2   | End position on the field           |
| Success  | Whether the event succeeded         |

You can download event CSV files (`.event.csv`) from the [RoboCup2D-data](https://github.com/hidehisaakiyama/RoboCup2D-data) repository or create your own CSV files from `.rcg` files using [`rcg2data`](https://github.com/hidehisaakiyama/rcg2data) tool.


You can upload multiple files at once. After selecting files, click **Upload**. When the upload succeeds, the page shows a success message.

![Upload success](images/usage_03_upload_2-success.png)

## 3. Register Uploaded Matches to a Tournament

Open a tournament from the Registered Tournaments list to open the tournament detail page.

If no match is registered yet, the page shows an empty state with an **Add Match** button.

![Tournament detail before match registration](images/usage_03_upload_3-register-csv-to-group.png)

Click **Add Match** to open the available match list. Select the matches you want to include in the tournament, then click **Add Selected Matches to Tournament**.

![CSV files selected](images/usage_03_upload_4-select-csv.png)


After registration, the tournament detail page lists the registered matches with buttons for **Match Selection & GIS Display** and **Full Data GIS Display**.

![Add matches to tournament](images/usage_03_upload_5-list.png)


## 4. Open Interactive GIS Analysis

There are two main ways to open the GIS screen:

- From the top page, click **GIS Analysis** to display all uploaded data.
- From a tournament or match list, open GIS for a specific match.

The GIS page displays event trajectories on a soccer field together with filter controls.

![GIS overview](images/usage_04_gis_1.png)

The main filters include:

| Filter         | Description                                   |
|----------------|-----------------------------------------------|
| Event Type     | Filter by type of event (pass, dribble, etc.) |
| Team           | Filter by team side                           |
| Player number  | Filter by uniform number                      |
| Match          | Filter by match ID (file name)                |
| Success        | Filter by event outcome                       |
| Time range     | Filter by start and end time                  |
| Display limit  | Maximum number of events to display           |
| Coordinate range | Bounding box filter on the field            |

After choosing conditions, click **Coordinate Search** to update the results.

## 5. Filter by Area on the Field

You can perform spatial filtering directly on the pitch.

Use the drawing tools on the left side of the map to create a **rectangle**, **circle**, or **polygon**. After drawing a shape, click **Search** to apply the spatial filter.

![Spatial filter drawing](images/usage_04_gis_2_filter.png)

The filtered result is reflected both on the pitch and in the list below the map.

![Spatial filter applied](images/usage_04_gis_4_after-filter.png)

To remove the spatial filter, click the **Clear** button next to the coordinate controls.

## 6. Export Results

Below the map, the **Event List** table shows the filtered events with fields such as start/end time, type, mode, team side, player number, positions, success flag, and match ID.

![Event list and map](images/usage_04_gis_2_map.png)

Click **Download CSV** to export the current search results as a CSV file.

## 7. Extract Event Sequences

ST-Pitch can extract a local event sequence from the current filtered area.

First, define the target area on the pitch using the spatial filter. Then click **Extract Sequence**.

![Sequence extraction mode](images/usage_05_sequence-1.png)

You can select a sequence in the sequence extraction mode by clicking on the map or the event list. When a sequence candidate is found, the extracted sequence is highlighted on the map.

![Sequence candidate highlighted](images/usage_05_sequence-2.png)

Click **Filter by Sequence** to limit the view to the extracted sequence only.

![Sequence filtered on the map](images/usage_05_sequence-3.png)

The **Event List** table then shows the ordered events in the sequence.

![Sequence event list](images/usage_05_sequence-4.png)

Click **Clear Sequence Filter** or **Exit Sequence Mode** to return to the normal GIS workflow.

