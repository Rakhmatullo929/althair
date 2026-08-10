import * as THREE from "three";

export type SolidGeometryAudit = {
  backCapTriangles: number;
  boundaryEdges: number;
  capTriangles: number;
  componentCount: number;
  depthSpread: number;
  degenerateTriangles: number;
  frontCapTriangles: number;
  inconsistentEdges: number;
  intentionalHoles: number;
  inwardCapTriangles: number;
  inwardComponents: number;
  isWatertight: boolean;
  maxComponentDepth: number;
  misalignedNormalVertices: number;
  minComponentDepth: number;
  nonManifoldEdges: number;
  outwardCapTriangles: number;
  sideTriangles: number;
  signedVolume: number;
  solidParts: number;
};

const LOGO_SCALE = 0.0255;
const TRIANGLE_AREA_EPSILON_SQ = 1e-20;
const VERTEX_WELD_TOLERANCE = 1e-7;
const COMPONENT_VOLUME_EPSILON = 1e-8;
const UNIFORM_DEPTH_TOLERANCE = 1e-6;
const EXPECTED_SOLID_PARTS = 6;
const EXPECTED_INTENTIONAL_HOLES = 3;

export function createSolidLogoGeometry(shapes: THREE.Shape[]) {
  const rawGeometry = new THREE.ExtrudeGeometry(shapes, {
    bevelEnabled: true,
    bevelSegments: 5,
    bevelSize: 1.8,
    bevelThickness: 2,
    curveSegments: 12,
    depth: 18,
    steps: 1,
  });

  // SVG uses a downward Y axis. A negative Y scale mirrors the vertex data,
  // reverses every triangle winding, and makes an otherwise closed extrusion
  // render inside-out. Two positive-determinant transforms preserve outward
  // winding while presenting the SVG upright to the camera.
  rawGeometry.scale(LOGO_SCALE, LOGO_SCALE, LOGO_SCALE);
  rawGeometry.rotateX(Math.PI);
  rawGeometry.center();

  const solidGeometry = removeDegenerateTriangles(rawGeometry);
  rawGeometry.dispose();
  solidGeometry.computeVertexNormals();
  solidGeometry.computeBoundingBox();
  solidGeometry.computeBoundingSphere();

  const intentionalHoles = shapes.reduce(
    (total, shape) => total + shape.holes.length,
    0,
  );
  const audit = auditSolidGeometry(
    solidGeometry,
    shapes.length,
    intentionalHoles,
  );

  if (!audit.isWatertight) {
    solidGeometry.dispose();
    throw new Error(
      `[Althair solid logo] Invalid closed geometry: ${JSON.stringify(audit)}`,
    );
  }

  solidGeometry.userData.solidAudit = audit;
  return solidGeometry;
}

function removeDegenerateTriangles(source: THREE.BufferGeometry) {
  const geometry = source.index ? source.toNonIndexed() : source;
  const position = geometry.getAttribute("position");
  const uv = geometry.getAttribute("uv");
  const cleanGeometry = new THREE.BufferGeometry();
  const cleanPositions: number[] = [];
  const cleanUvs: number[] = [];
  const pointA = new THREE.Vector3();
  const pointB = new THREE.Vector3();
  const pointC = new THREE.Vector3();
  const edgeOne = new THREE.Vector3();
  const edgeTwo = new THREE.Vector3();
  const faceNormal = new THREE.Vector3();
  const groups = geometry.groups.length
    ? geometry.groups
    : [{ count: position.count, materialIndex: 0, start: 0 }];

  for (const group of groups) {
    const cleanGroupStart = cleanPositions.length / 3;
    const groupEnd = group.start + group.count;

    for (let vertex = group.start; vertex < groupEnd; vertex += 3) {
      pointA.fromBufferAttribute(position, vertex);
      pointB.fromBufferAttribute(position, vertex + 1);
      pointC.fromBufferAttribute(position, vertex + 2);
      edgeOne.subVectors(pointB, pointA);
      edgeTwo.subVectors(pointC, pointA);
      faceNormal.crossVectors(edgeOne, edgeTwo);

      if (faceNormal.lengthSq() <= TRIANGLE_AREA_EPSILON_SQ) continue;

      for (let offset = 0; offset < 3; offset += 1) {
        const sourceVertex = vertex + offset;
        cleanPositions.push(
          position.getX(sourceVertex),
          position.getY(sourceVertex),
          position.getZ(sourceVertex),
        );
        if (uv) cleanUvs.push(uv.getX(sourceVertex), uv.getY(sourceVertex));
      }
    }

    const cleanGroupCount = cleanPositions.length / 3 - cleanGroupStart;
    if (cleanGroupCount > 0) {
      cleanGeometry.addGroup(
        cleanGroupStart,
        cleanGroupCount,
        group.materialIndex ?? 0,
      );
    }
  }

  cleanGeometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(cleanPositions, 3),
  );
  if (cleanUvs.length > 0) {
    cleanGeometry.setAttribute(
      "uv",
      new THREE.Float32BufferAttribute(cleanUvs, 2),
    );
  }

  if (geometry !== source) geometry.dispose();
  return cleanGeometry;
}

function auditSolidGeometry(
  geometry: THREE.BufferGeometry,
  solidParts: number,
  intentionalHoles: number,
): SolidGeometryAudit {
  const position = geometry.getAttribute("position");
  const normal = geometry.getAttribute("normal");
  const triangleCount = position.count / 3;
  const materialByTriangle = new Int16Array(triangleCount).fill(-1);
  const vertexIds = new Map<string, number>();
  const edges = new Map<string, { balance: number; count: number }>();
  const parents: number[] = [];
  const ranks: number[] = [];
  const vertexDepths: number[] = [];
  const triangleRoots: number[] = [];
  const triangleVolumes: number[] = [];
  const pointA = new THREE.Vector3();
  const pointB = new THREE.Vector3();
  const pointC = new THREE.Vector3();
  const edgeOne = new THREE.Vector3();
  const edgeTwo = new THREE.Vector3();
  const faceNormal = new THREE.Vector3();
  const volumeCross = new THREE.Vector3();

  for (const group of geometry.groups) {
    const firstTriangle = group.start / 3;
    const endTriangle = firstTriangle + group.count / 3;
    materialByTriangle.fill(
      group.materialIndex ?? 0,
      firstTriangle,
      endTriangle,
    );
  }

  const find = (vertex: number): number => {
    let root = vertex;
    while (parents[root] !== root) root = parents[root];
    let current = vertex;
    while (parents[current] !== current) {
      const next = parents[current];
      parents[current] = root;
      current = next;
    }
    return root;
  };

  const union = (left: number, right: number) => {
    let leftRoot = find(left);
    let rightRoot = find(right);
    if (leftRoot === rightRoot) return;
    if (ranks[leftRoot] < ranks[rightRoot]) {
      [leftRoot, rightRoot] = [rightRoot, leftRoot];
    }
    parents[rightRoot] = leftRoot;
    if (ranks[leftRoot] === ranks[rightRoot]) ranks[leftRoot] += 1;
  };

  const resolveVertexId = (point: THREE.Vector3) => {
    const key = [point.x, point.y, point.z]
      .map((value) => Math.round(value / VERTEX_WELD_TOLERANCE))
      .join(":");
    const existing = vertexIds.get(key);
    if (existing !== undefined) return existing;
    const vertex = vertexIds.size;
    vertexIds.set(key, vertex);
    parents.push(vertex);
    ranks.push(0);
    vertexDepths.push(point.z);
    return vertex;
  };

  const registerEdge = (start: number, end: number) => {
    const low = Math.min(start, end);
    const high = Math.max(start, end);
    const key = `${low}:${high}`;
    const edge = edges.get(key) ?? { balance: 0, count: 0 };
    edge.count += 1;
    edge.balance += start === low ? 1 : -1;
    edges.set(key, edge);
  };

  let backCapTriangles = 0;
  let capTriangles = 0;
  let degenerateTriangles = 0;
  let frontCapTriangles = 0;
  let inwardCapTriangles = 0;
  let misalignedNormalVertices = 0;
  let outwardCapTriangles = 0;
  let sideTriangles = 0;
  let signedVolume = 0;

  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    const vertex = triangle * 3;
    pointA.fromBufferAttribute(position, vertex);
    pointB.fromBufferAttribute(position, vertex + 1);
    pointC.fromBufferAttribute(position, vertex + 2);
    edgeOne.subVectors(pointB, pointA);
    edgeTwo.subVectors(pointC, pointA);
    faceNormal.crossVectors(edgeOne, edgeTwo);

    if (faceNormal.lengthSq() <= TRIANGLE_AREA_EPSILON_SQ) {
      degenerateTriangles += 1;
      continue;
    }

    faceNormal.normalize();
    for (let offset = 0; offset < 3; offset += 1) {
      const normalX = normal.getX(vertex + offset);
      const normalY = normal.getY(vertex + offset);
      const normalZ = normal.getZ(vertex + offset);
      const alignment =
        faceNormal.x * normalX +
        faceNormal.y * normalY +
        faceNormal.z * normalZ;
      if (alignment < 0.999) misalignedNormalVertices += 1;
    }

    const ids = [
      resolveVertexId(pointA),
      resolveVertexId(pointB),
      resolveVertexId(pointC),
    ];
    union(ids[0], ids[1]);
    union(ids[1], ids[2]);
    registerEdge(ids[0], ids[1]);
    registerEdge(ids[1], ids[2]);
    registerEdge(ids[2], ids[0]);

    const triangleVolume =
      pointA.dot(volumeCross.crossVectors(pointB, pointC)) / 6;
    signedVolume += triangleVolume;
    triangleRoots.push(ids[0]);
    triangleVolumes.push(triangleVolume);

    if (materialByTriangle[triangle] === 0) {
      capTriangles += 1;
      const centroidZ = (pointA.z + pointB.z + pointC.z) / 3;
      if (centroidZ > 0 && faceNormal.z > 0) {
        frontCapTriangles += 1;
        outwardCapTriangles += 1;
      } else if (centroidZ < 0 && faceNormal.z < 0) {
        backCapTriangles += 1;
        outwardCapTriangles += 1;
      } else {
        inwardCapTriangles += 1;
      }
    } else {
      sideTriangles += 1;
    }
  }

  let boundaryEdges = 0;
  let inconsistentEdges = 0;
  let nonManifoldEdges = 0;
  for (const edge of edges.values()) {
    if (edge.count === 1) boundaryEdges += 1;
    if (edge.count > 2) nonManifoldEdges += 1;
    if (edge.count === 2 && edge.balance !== 0) inconsistentEdges += 1;
  }

  const componentVolumes = new Map<number, number>();
  for (let index = 0; index < triangleRoots.length; index += 1) {
    const root = find(triangleRoots[index]);
    componentVolumes.set(
      root,
      (componentVolumes.get(root) ?? 0) + triangleVolumes[index],
    );
  }
  const inwardComponents = [...componentVolumes.values()].filter(
    (volume) => volume <= COMPONENT_VOLUME_EPSILON,
  ).length;
  const componentCount = componentVolumes.size;
  const componentDepthRanges = new Map<
    number,
    { maximum: number; minimum: number }
  >();
  for (let vertex = 0; vertex < vertexDepths.length; vertex += 1) {
    const root = find(vertex);
    const depth = vertexDepths[vertex];
    const range = componentDepthRanges.get(root) ?? {
      maximum: depth,
      minimum: depth,
    };
    range.maximum = Math.max(range.maximum, depth);
    range.minimum = Math.min(range.minimum, depth);
    componentDepthRanges.set(root, range);
  }
  const componentDepths = [...componentDepthRanges.values()].map(
    ({ maximum, minimum }) => maximum - minimum,
  );
  const minComponentDepth = Math.min(...componentDepths);
  const maxComponentDepth = Math.max(...componentDepths);
  const depthSpread = maxComponentDepth - minComponentDepth;

  const isWatertight =
    boundaryEdges === 0 &&
    nonManifoldEdges === 0 &&
    inconsistentEdges === 0 &&
    degenerateTriangles === 0 &&
    inwardComponents === 0 &&
    inwardCapTriangles === 0 &&
    misalignedNormalVertices === 0 &&
    frontCapTriangles > 0 &&
    backCapTriangles === frontCapTriangles &&
    sideTriangles > 0 &&
    componentCount === solidParts &&
    solidParts === EXPECTED_SOLID_PARTS &&
    intentionalHoles === EXPECTED_INTENTIONAL_HOLES &&
    minComponentDepth > 0 &&
    depthSpread <= UNIFORM_DEPTH_TOLERANCE &&
    signedVolume > COMPONENT_VOLUME_EPSILON;

  return {
    backCapTriangles,
    boundaryEdges,
    capTriangles,
    componentCount,
    depthSpread,
    degenerateTriangles,
    frontCapTriangles,
    inconsistentEdges,
    intentionalHoles,
    inwardCapTriangles,
    inwardComponents,
    isWatertight,
    maxComponentDepth,
    misalignedNormalVertices,
    minComponentDepth,
    nonManifoldEdges,
    outwardCapTriangles,
    sideTriangles,
    signedVolume,
    solidParts,
  };
}
