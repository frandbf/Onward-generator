<?xml version="1.0" encoding="ISO-8859-1"?>
<!-- Synthetic Catastro INSPIRE BU Building fixture (CC0-1.0). Not real cadastre data.
     One Building per parcel of synthetic_bu.gml; geometry left out, it is not read. -->
<gml:FeatureCollection gml:id="ES.SDGC.BU" xmlns:base="urn:x-inspire:specification:gmlas:BaseTypes:3.2" xmlns:bu-core2d="http://inspire.jrc.ec.europa.eu/schemas/bu-core2d/2.0" xmlns:bu-ext2d="http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <!-- Parcel of P1 and P2 (way 101 / 108): retail, a few dwellings above. -->
  <gml:featureMember><bu-ext2d:Building gml:id="ES.SDGC.BU.0000001AA0000A">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000001AA0000A</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:currentUse>4_2_retail</bu-ext2d:currentUse>
    <bu-ext2d:numberOfBuildingUnits>9</bu-ext2d:numberOfBuildingUnits>
    <bu-ext2d:numberOfDwellings>8</bu-ext2d:numberOfDwellings>
    <bu-ext2d:numberOfFloorsAboveGround xsi:nil="true" nilReason="other:unpopulated"></bu-ext2d:numberOfFloorsAboveGround>
  </bu-ext2d:Building></gml:featureMember>
  <!-- Parcel of P5 (relation 201): residential, many dwellings. -->
  <gml:featureMember><bu-ext2d:Building gml:id="ES.SDGC.BU.0000004AA0000A">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000004AA0000A</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:currentUse>1_residential</bu-ext2d:currentUse>
    <bu-ext2d:numberOfBuildingUnits>58</bu-ext2d:numberOfBuildingUnits>
    <bu-ext2d:numberOfDwellings>55</bu-ext2d:numberOfDwellings>
  </bu-ext2d:Building></gml:featureMember>
  <!-- Parcel of P4: office, counts left nil. -->
  <gml:featureMember><bu-ext2d:Building gml:id="ES.SDGC.BU.0000003AA0000A">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000003AA0000A</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:currentUse>4_1_office</bu-ext2d:currentUse>
    <bu-ext2d:numberOfBuildingUnits xsi:nil="true" nilReason="other:unpopulated"></bu-ext2d:numberOfBuildingUnits>
  </bu-ext2d:Building></gml:featureMember>
  <!-- Repeated localId: ignored. -->
  <gml:featureMember><bu-ext2d:Building gml:id="ES.SDGC.BU.0000001AA0000A.b">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000001AA0000A</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:currentUse>1_residential</bu-ext2d:currentUse>
  </bu-ext2d:Building></gml:featureMember>
  <!-- Malformed: no current use. -->
  <gml:featureMember><bu-ext2d:Building gml:id="ES.SDGC.BU.0000005AA0000A">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000005AA0000A</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:currentUse xsi:nil="true"></bu-ext2d:currentUse>
  </bu-ext2d:Building></gml:featureMember>
  <!-- Malformed: a negative count. -->
  <gml:featureMember><bu-ext2d:Building gml:id="ES.SDGC.BU.0000006AA0000A">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000006AA0000A</base:localId><base:namespace>ES.SDGC.BU</base:namespace></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:currentUse>1_residential</bu-ext2d:currentUse>
    <bu-ext2d:numberOfDwellings>-1</bu-ext2d:numberOfDwellings>
  </bu-ext2d:Building></gml:featureMember>
  <!-- Malformed: no localId. -->
  <gml:featureMember><bu-ext2d:Building gml:id="ES.SDGC.BU.x">
    <bu-ext2d:currentUse>4_2_retail</bu-ext2d:currentUse>
  </bu-ext2d:Building></gml:featureMember>
  <!-- A part is not a building. -->
  <gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0000007AA0000A_part1">
    <bu-core2d:inspireId><base:Identifier><base:localId>0000007AA0000A_part1</base:localId></base:Identifier></bu-core2d:inspireId>
    <bu-ext2d:currentUse>4_2_retail</bu-ext2d:currentUse>
  </bu-ext2d:BuildingPart></gml:featureMember>
</gml:FeatureCollection>
