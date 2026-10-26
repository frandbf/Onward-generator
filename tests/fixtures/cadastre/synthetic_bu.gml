<?xml version="1.0" encoding="ISO-8859-1"?>
<!-- Synthetic Catastro INSPIRE BU BuildingPart fixture (CC0-1.0). Not real cadastre data.
     Coordinates are EPSG:25830 = MURCIA_CENTER origin (664343.977, 4205699.652) + local metres. -->
<gml:FeatureCollection gml:id="ES.SDGC.BU" xmlns:base="urn:x-inspire:specification:gmlas:BaseTypes:3.2" xmlns:bu-core2d="http://inspire.jrc.ec.europa.eu/schemas/bu-core2d/2.0" xmlns:bu-ext2d="http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">
  <!-- P1: local x -50..-25, y -50..-20, 5 floors (way 101 / 108). -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000001AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000001AA0000A_part1</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S1" srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="2" count="5">664293.98 4205649.65 664318.98 4205649.65 664318.98 4205679.65 664293.98 4205679.65 664293.98 4205649.65</gml:posList>
    </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsAboveGround>5</bu-ext2d:numberOfFloorsAboveGround>
    <bu-ext2d:heightBelowGround uom="m">3</bu-ext2d:heightBelowGround>
    <bu-ext2d:numberOfFloorsBelowGround>1</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
  <!-- P2: local x -25..-20, y -50..-20, 2 floors (same parcel). -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000001AA0000A_part2">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000001AA0000A_part2</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S2" srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="2" count="5">664318.98 4205649.65 664323.98 4205649.65 664323.98 4205679.65 664318.98 4205679.65 664318.98 4205649.65</gml:posList>
    </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsAboveGround>2</bu-ext2d:numberOfFloorsAboveGround>
    <bu-ext2d:heightBelowGround uom="m">0</bu-ext2d:heightBelowGround>
    <bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
  <!-- P3: over way 102, but 0 floors above ground (underground only). -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000002AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000002AA0000A_part1</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S3" srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="2" count="5">664423.98 4205699.65 664463.98 4205699.65 664463.98 4205729.65 664423.98 4205729.65 664423.98 4205699.65</gml:posList>
    </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsAboveGround>0</bu-ext2d:numberOfFloorsAboveGround>
    <bu-ext2d:heightBelowGround uom="m">3</bu-ext2d:heightBelowGround>
    <bu-ext2d:numberOfFloorsBelowGround>1</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
  <!-- P4: covers only a third of way 104 (x 110..115), 3 floors. -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000003AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000003AA0000A_part1</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S4" srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="2" count="5">664453.98 4205639.65 664458.98 4205639.65 664458.98 4205654.65 664453.98 4205654.65 664453.98 4205639.65</gml:posList>
    </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsAboveGround>3</bu-ext2d:numberOfFloorsAboveGround>
    <bu-ext2d:heightBelowGround uom="m">0</bu-ext2d:heightBelowGround>
    <bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
  <!-- P5: relation 201 outline (x 30..70, y -40..0) with a 5 x 5 m interior ring, 7 floors. -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000004AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000004AA0000A_part1</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S5" srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch>
      <gml:exterior><gml:LinearRing><gml:posList srsDimension="2" count="5">664373.98 4205659.65 664413.98 4205659.65 664413.98 4205699.65 664373.98 4205699.65 664373.98 4205659.65</gml:posList></gml:LinearRing></gml:exterior>
      <gml:interior><gml:LinearRing><gml:posList srsDimension="2" count="5">664388.98 4205674.65 664388.98 4205679.65 664393.98 4205679.65 664393.98 4205674.65 664388.98 4205674.65</gml:posList></gml:LinearRing></gml:interior>
    </gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsAboveGround>7</bu-ext2d:numberOfFloorsAboveGround>
    <bu-ext2d:heightBelowGround uom="m">0</bu-ext2d:heightBelowGround>
    <bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
  <!-- Malformed: no floor count. -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000005AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000005AA0000A_part1</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S6" srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="2" count="5">664043.98 4205399.65 664053.98 4205399.65 664053.98 4205409.65 664043.98 4205409.65 664043.98 4205399.65</gml:posList>
    </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
  <!-- Invalid: bow-tie ring. -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000006AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000006AA0000A_part1</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S7" srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="2" count="5">664043.98 4205399.65 664053.98 4205409.65 664053.98 4205399.65 664043.98 4205409.65 664043.98 4205399.65</gml:posList>
    </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsAboveGround>2</bu-ext2d:numberOfFloorsAboveGround>
    <bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
  <!-- Wrong CRS: geographic degrees must not be read as metres. -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000007AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000007AA0000A_part1</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S8" srsName="urn:ogc:def:crs:EPSG::4326"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="2" count="5">37.98407 -1.12863 37.98417 -1.12863 37.98417 -1.12853 37.98407 -1.12853 37.98407 -1.12863</gml:posList>
    </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsAboveGround>2</bu-ext2d:numberOfFloorsAboveGround>
    <bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
  <!-- Duplicate of P1. -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000001AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000001AA0000A_part1</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S9" srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="2" count="5">664293.98 4205649.65 664318.98 4205649.65 664318.98 4205679.65 664293.98 4205679.65 664293.98 4205649.65</gml:posList>
    </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
    <bu-ext2d:numberOfFloorsAboveGround>9</bu-ext2d:numberOfFloorsAboveGround>
    <bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>
  </bu-ext2d:BuildingPart></gml:featureMember>
</gml:FeatureCollection>
